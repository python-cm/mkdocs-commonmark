"""
HTML renderer for mistletoe.
"""

import re
import sys
from itertools import chain
from urllib.parse import quote
from mistletoe.block_token import HTMLBlock
from mistletoe.span_token import HTMLSpan
from mistletoe.base_renderer import BaseRenderer
if sys.version_info < (3, 4):
    from mistletoe import _html as html
else:
    import html


import markdown
from markdown import util
from markdown.util import etree, text_type, AtomicString
from mistletoe import Document

def splice(x):
    # unfortunately, str is iterable
    if (isinstance(x, util.text_type) or
        # fortunately, Element is iterable but has no __iter__
        getattr(x, '__iter__', None) is None):
        yield x
    else:
        for t in x:
            yield from splice(t)

def safe_concat(a, b):
    ''' to deal with cases when a is None '''
    if isinstance(a, AtomicString):
        return AtomicString(a + b)
    else:
        # otherwise a is str-like or none
        return (a or '') + b


def splice(x):
    # unfortunately, str is iterable
    if (isinstance(x, text_type) or
        # fortunately, Element is iterable but has no __iter__
        getattr(x, '__iter__', None) is None):
        yield x
    else:
        for t in x:
            yield from splice(t)


class ETreeRenderer(BaseRenderer):
    """
    ElementTree renderer class.

    This is for directly converting into an ElementTree for Python-Markdown to further process.
    """
    def __init__(self, *extras):
        """
        Args:
            extras (list): allows subclasses to add even more custom tokens.
        """
        self._suppress_ptag_stack = [False]
        super().__init__(*chain((HTMLBlock, HTMLSpan), extras))
        # html.entities.html5 includes entitydefs not ending with ';',
        # CommonMark seems to hate them, so...
        self._stdlib_charref = html._charref
        _charref = re.compile(r'&(#[0-9]+;'
                              r'|#[xX][0-9a-fA-F]+;'
                              r'|[^\t\n\f <&#;]{1,32};)')
        html._charref = _charref

    def __exit__(self, *args):
        super().__exit__(*args)
        html._charref = self._stdlib_charref

    def render_to_plain(self, token):
        if hasattr(token, 'children'):
            inner = [self.render_to_plain(child) for child in token.children]
            return ''.join(inner)
        return self.escape_html(token.content)

    def render_inner(self, token):
        """
        Here we have to just return a list of ctree.Element!
        """
        return map(self.render, token.children)

    def render_inner_join(self, token, delim='\n'):
        """
        Render as usual, but join yielded elements with
        a newline character in between.
        """
        xs = self.render_inner(token)

        try:
            head = next(xs)
        except StopIteration:
            # if the iterator is empty, we are done
            return

        yield head
        for x in xs:
            yield AtomicString(delim)
            yield x

    def append_newline_inside(self, el):
        # is it non-empty?
        if el:
            if el[-1].tail is None:
                el[-1].tail = AtomicString('\n')
            elif isinstance(el[-1].tail, AtomicString):
                el[-1].tail = AtomicString(el[-1].tail + '\n')
            else:
                el[-1].tail += '\n'
        # does it have text?
        elif el.text:
            el.text += '\n'
        # absolutely nothing, do nothing
        return el

    def append_elems(self, el, inner):
        # last-seen etree.Element to append strings to
        prev = None
        # FIXME: to truely ensure that (not) using atomic strings
        # is okay here; if the buffer is str but the buffer is not,
        # it would be impossible to distinguish them without creating
        # a new element!
        buf = ''

        for t in splice(inner):
            if isinstance(t, util.text_type):
                buf += t
            else:
                # must be an etree.Element
                if buf:
                    if prev is None:
                        el.text = safe_concat(el.text, buf)
                    else:
                        prev.tail = safe_concat(prev.tail, buf)
                    buf = ''
                el.append(t)
                prev = t

        if buf:
            if prev is None:
                el.text = safe_concat(el.text, buf)
            else:
                prev.tail = safe_concat(prev.tail, buf)

        return el

    def render_strong(self, token):
        el = etree.Element('strong')
        return self.append_elems(el, self.render_inner(token))

    def render_emphasis(self, token):
        el = etree.Element('em')
        return self.append_elems(el, self.render_inner(token))

    def render_inline_code(self, token):
        el = etree.Element('code')
        el.text = AtomicString(html.escape(token.children[0].content)
            .replace('&#x27;', "'"))
        return el

    def render_strikethrough(self, token):
        el = etree.Element('del')
        return self.append_elems(el, self.render_inner(token))

    def render_image(self, token):
        # note that the attributes are sorted before output HTML,
        # NOT by the order specified. annoying when taking diffs,
        # requiring a customized HTML serializer
        el = etree.Element('img', src=token.src, alt=self.render_to_plain(token))
        if token.title:
            el.set('title', self.escape_html(token.title))
        return el

    def render_link(self, token):
        el = etree.Element('a', href=self.escape_url(token.target))
        if token.title:
            el.set('title', self.escape_html(token.title))
        self.append_elems(el, self.render_inner(token))
        return el

    def render_auto_link(self, token):
        el = etree.Element('a')
        if token.mailto:
            target = 'mailto:{}'.format(token.target)
        else:
            target = self.escape_url(token.target)
        el.set('href', target)
        return self.append_elems(el, self.render_inner(token))

    def render_escape_sequence(self, token):
        return self.render_inner(token)

    def render_raw_text(self, token):
        return AtomicString(self.escape_html(token.content))

    @staticmethod
    def render_html_span(token):
        # Not used at all???
        return token.content

    def render_heading(self, token):
        el = etree.Element('h{}'.format(token.level))
        self.append_elems(el, self.render_inner(token))
        return el

    def render_quote(self, token):
        el = etree.Element('blockquote')
        el.text = '\n'
        self._suppress_ptag_stack.append(False)
        self.append_elems(el, self.render_inner_join(token))
        self.append_newline_inside(el)
        # remove duplicate newlines, dealing with empty blockquote, e.g.
        # test #202: '>\n' -> '<blockquote>\n</blockquote>\n'
        if not el and el.text == '\n\n':
            el.text = AtomicString('\n')
        self._suppress_ptag_stack.pop()
        return el

    def render_paragraph(self, token):
        if self._suppress_ptag_stack[-1]:
            # FIXME: can I not returning a list here :( ?
            return self.render_inner(token)

        el = etree.Element('p')
        return self.append_elems(el, self.render_inner(token))

    def render_block_code(self, token):
        el_pre = etree.Element('pre')
        el_code = etree.SubElement(el_pre, 'code')
        if token.language:
            el_code.set('class', 'language-{}'.format(self.escape_html(token.language)))
        # to comply with the format using in PythonMarkdown.
        # or it may break in plugins like codehilite!!
        code_text = (html.escape(token.children[0].content)
            .replace('&#x27;', "'")
            # FIXME: breaks commonmark test suite #176
            .replace('&quot;', '"'))
        # protect inside content from being interpreted
        el_code.text = AtomicString(code_text)
        return el_pre

    def render_list(self, token):
        if token.start is not None:
            el = etree.Element('ol')
            if token.start != 1:
                el.set('start', str(token.start))
        else:
            el = etree.Element('ul')

        el.text = '\n'

        self._suppress_ptag_stack.append(not token.loose)
        self.append_elems(el, self.render_inner_join(token))
        self.append_newline_inside(el)
        self._suppress_ptag_stack.pop()

        return el

    def render_list_item(self, token):
        el = etree.Element('li')
        if not token.children:
            return el

        # to be less confusing, the original control flow is retained,
        # and manipulations are kept as comments
        inner = ['\n', self.render_inner_join(token), '\n']
        # inner_template = '\n{}\n'
        if self._suppress_ptag_stack[-1]:
            if token.children[0].__class__.__name__ == 'Paragraph':
                # inner_template = inner_template[1:]
                inner = inner[1:]
            if token.children[-1].__class__.__name__ == 'Paragraph':
                # inner_template = inner_template[:-1]
                inner = inner[:-1]

        self.append_elems(el, inner)

        return el

    def render_table(self, token):
        # This is actually gross and I wonder if there's a better way to do it.
        #
        # The primary difficulty seems to be passing down alignment options to
        # reach individual cells.
        el = etree.Element('table')
        el.text = '\n'
        if hasattr(token, 'header'):
            thead = etree.SubElement(el, 'thead')
            thead.text = '\n'
            thead.tail = '\n'
            row = self.render_table_row(token.header, is_header=True)
            thead.append(row)

        tbody = etree.SubElement(el, 'tbody')
        tbody.text = '\n'
        tbody.tail = '\n'
        self.append_elems(tbody, self.render_inner(token))

        return el

    def render_table_row(self, token, is_header=False):
        el = etree.Element('tr')
        el.text = '\n'
        el.tail = '\n'
        inner = [self.render_table_cell(child, is_header)
                 for child in token.children]
        return self.append_elems(el, inner)

    def render_table_cell(self, token, in_header=False):
        el = etree.Element('th' if in_header else 'td')
        el.tail = '\n'

        if token.align is None:
            el.set('align', 'left')
        elif token.align == 0:
            el.set('align', 'center')
        elif token.align == 1:
            el.set('align', 'right')

        return self.append_elems(el, self.render_inner(token))

    @staticmethod
    def render_thematic_break(token):
        return etree.Element('hr')

    @staticmethod
    def render_line_break(token):
        if token.soft:
            return AtomicString('\n')
        el = etree.Element('br')
        el.tail = '\n'
        return el

    @staticmethod
    def render_html_block(token):
        # XXX?
        return AtomicString(token.content)

    def render_document(self, token):
        self.footnotes.update(token.footnotes)
        # python-markdown recognizes and strips *this* hardcoded <div>
        el = etree.Element('div')
        self.append_elems(el, self.render_inner_join(token))
        self.append_newline_inside(el)
        elt = etree.ElementTree(el)
        return elt

    @staticmethod
    def escape_html(raw):
        return html.escape(html.unescape(raw)).replace('&#x27;', "'")

    @staticmethod
    def escape_url(raw):
        """
        Escape urls to prevent code injection craziness. (Hopefully.)
        """
        return html.escape(quote(html.unescape(raw), safe='/#:()*?=%@+,&'))


class MarkdownInterop(markdown.Markdown):
    def __init__(self, **kwargs):
        super(MarkdownInterop, self).__init__(**kwargs)
        # XXX: change to allow only selected built-in pre-processors
        self.preprocessors.deregister('normalize_whitespace')
        self.preprocessors.deregister('html_block')
        # will prevent mistletoe's detection from working when
        # CodeHilite is NOT used.
        # it is garentreed to have been registered by mkdocs,
        # so the slient option is not required
        from markdown.extensions.codehilite import CodeHiliteExtension
        codehilite = False
        for ext in self.registeredExtensions:
            if isinstance(ext, CodeHiliteExtension):
                codehilite_found = True
                break
        else:
            self.preprocessors.deregister('fenced_code_block')

    def _convert_to_elem(self, source):
        ''' Run the convert step until block parsing is done.
            only useful for introspecting. '''

        if not source.strip():
            return ''  # a blank unicode string

        try:
            source = util.text_type(source)
        except UnicodeDecodeError as e:  # pragma: no cover
            # Customise error message while maintaining original trackback
            e.reason += '. -- Note: Markdown only accepts unicode input!'
            raise

        # Split into lines and run the line preprocessors.
        self._run_preprocessors(source, concat=False)

        # Parse the high-level elements.
        root = self.parser.parseDocument(self.lines).getroot()

        return root

    def _run_preprocessors(self, source, concat=True):
        _lines = source.split("\n")
        # do our own normalization here
        _lines = [l.replace(util.STX, '').replace(util.ETX, '')
                  for l in _lines]
        self.lines = _lines

        for prep in self.preprocessors:
            self.lines = prep.run(self.lines)
            # because we don't use the default normalizing preprocessor,
            # note the edge case that footnote plugin complains if the
            # document turns out to be empty
            if not self.lines:
                self.lines.append('')
        if concat:
            return '\n'.join(self.lines)

    def _convert_from_elem(self, root):
        # Run the tree-processors
        for treeprocessor in self.treeprocessors:
            newRoot = treeprocessor.run(root)
            if newRoot is not None:
                root = newRoot

        # Serialize _properly_.  Strip top-level tags.
        output = self.serializer(root)
        if self.stripTopLevelTags:
            try:
                start = output.index(
                    '<%s>' % self.doc_tag) + len(self.doc_tag) + 2
                end = output.rindex('</%s>' % self.doc_tag)
                output = output[start:end].strip()
            except ValueError:  # pragma: no cover
                if output.strip().endswith('<%s />' % self.doc_tag):
                    # We have an empty document
                    output = ''
                else:
                    # We have a serious problem
                    raise ValueError('Markdown failed to strip top-level '
                                     'tags. Document=%r' % output.strip())

        # Run the text post-processors
        for pp in self.postprocessors:
            output = pp.run(output)

        return output.strip()
