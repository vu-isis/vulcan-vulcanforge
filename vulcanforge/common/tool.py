from urllib import basejoin

from vulcanforge.common.helpers import slugify


class ConfigOption(object):

    def __init__(self, name, ming_type, default):
        self.name = name
        self.ming_type = ming_type
        self._default = default

    @property
    def default(self):
        if callable(self._default):
            return self._default()
        return self._default


class SitemapEntry(object):

    def __init__(self, label, url=None, children=None, className=None,
                 ui_icon=None, small=None, icon_url=None, icon_id=None):
        self.label = label
        self.className = className
        self.url = url
        self.small = small
        self.ui_icon = ui_icon
        self.icon_url = icon_url
        if children is None:
            children = []
        self.children = children
        if icon_id is None and isinstance(label, basestring):
            icon_id = slugify(label)
        self.icon_id = icon_id

    def __getitem__(self, x):
        if isinstance(x, (list, tuple)):
            self.children.extend(list(x))
        else:
            self.children.append(x)
        return self

    def __repr__(self):
        return '\n'.join((
            '<SitemapEntry ',
            '    label=%r' % self.label,
            '    children=%s' % repr(self.children).replace('\n', '\n    '),
            '>'
        ))

    def bind_app(self, app):
        lbl = self.label
        url = self.url
        if callable(lbl):
            lbl = lbl(app)
        if url is not None:
            url = basejoin(app.url, url)
        icon_id = self.icon_id
        if icon_id is None:
            icon_id = slugify(lbl)
        return SitemapEntry(
            lbl, url,
            [ch.bind_app(app) for ch in self.children],
            className=self.className,
            icon_id=icon_id
        )

    def extend(self, sitemap):
        child_index = dict(
            (ch.label, ch) for ch in self.children)
        for e in sitemap:
            lbl = e.label
            match = child_index.get(e.label)
            if match and match.url == e.url:
                match.extend(e.children)
            else:
                self.children.append(e)
                child_index[lbl] = e
