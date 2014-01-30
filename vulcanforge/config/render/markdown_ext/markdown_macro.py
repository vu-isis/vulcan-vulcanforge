# -*- coding: utf-8 -*-
import cgi
import shlex
import string
import logging
import re

import pymongo
from pylons import tmpl_context as c, app_globals as g, request
import ew

from vulcanforge.common.helpers import encode_keys, ago
from vulcanforge.artifact.model import Shortlink, Feed
from vulcanforge.common.util.urls import rebase_url
from vulcanforge.project.model import Project, ProjectCategory
from vulcanforge.project.widgets import ProjectListWidget

LOG = logging.getLogger(__name__)

_macros = {}


RELATIVE_OR_ABSOLUTE_URL_REGEX = re.compile('^\.{0,2}/')


class Include(ew.Widget):
    template = 'jinja:vulcanforge:common/templates/widgets/include.html'
    params = ['artifact', 'attrs']
    artifact = None
    attrs = {
        'style': 'width:270px;float:right;background-color:#ccc'
    }


class macro(object):
    def __init__(self, context=None):
        self._context = context

    def __call__(self, func):
        _macros[func.__name__] = (func, self._context)
        return func


class parse(object):
    def __init__(self, context):
        self._context = context

    def __call__(self, s):
        try:
            if s.startswith('quote '):
                return '[[' + s[len('quote '):] + ']]'
            try:
                parts = [unicode(x, 'utf-8')
                         for x in shlex.split(s.encode('utf-8'))]
                if not parts:
                    return '[[' + s + ']]'
                macro = self._lookup_macro(parts[0])
                if not macro:
                    return '[[' + s + ']]'
                for t in parts[1:]:
                    if '=' not in t:
                        return '[-%s: missing =-]' % ' '.join(parts)
                args = dict(t.split('=', 1) for t in parts[1:])
                response = macro(**encode_keys(args))
                return response
            except (ValueError, TypeError), ex:
                msg = cgi.escape(u'[[%s]] (%s)' % (s, repr(ex)))
                xml = '<pre><code>{}</code></pre>'.format(msg)
                return '\n<div class="error">{}</div>'.format(xml)
        except Exception, ex:
            raise
            #return '[[Error parsing %s: %s]]' % (s, ex)

    def _lookup_macro(self, s):
        macro, context = _macros.get(s, None)
        if context is None or context == self._context:
            return macro
        else:
            return None


template_neighborhood_feeds = string.Template('''
<div class="neighborhood_feed_entry">
<h3><a href="$href">$title</a></h3>
<p>
by <em>$author</em>
<small>$ago</small>
</p>
<p>$description</p>
</div>
''')


@macro('neighborhood-wiki')
def neighborhood_feeds(tool_name, max_number=5, sort='pubdate'):
    feed = Feed.query.find(
        dict(
            tool_name=tool_name,
            neighborhood_id=c.project.neighborhood._id))
    feed = feed.sort(sort, pymongo.DESCENDING).limit(int(max_number)).all()
    output = '\n'.join(
        template_neighborhood_feeds.substitute(dict(
            href=item.link,
            title=item.title,
            author=item.author_name,
            ago=ago(item.pubdate),
            description=item.description))
        for item in feed)
    return output


@macro('neighborhood-wiki')
def projects(category=None, display_mode='grid', sort='last_updated',
             labels=''):
    q = dict(
        neighborhood_id=c.project.neighborhood_id,
        deleted=False,
        shortname={'$ne': '--init--'})
    if labels:
        or_labels = labels.split('|')
        q['$or'] = [{'labels': {'$all': l.split(',')}} for l in or_labels]
    if category is not None:
        category = ProjectCategory.query.get(name=category)
    if category is not None:
        q['category_id'] = category._id
    pq = Project.query.find(q)
    pq = pq.limit(100)
    if sort == 'alpha':
        pq.sort('name')
    else:
        pq.sort('last_updated', pymongo.DESCENDING)
    pl = ProjectListWidget()
    g.resource_manager.register(pl)
    response = pl.display(projects=pq.all(), display_mode=display_mode)
    return response


@macro()
def img(src=None, **kw):
    attrs = ('%s="%s"' % t for t in kw.iteritems())
    try:
        included = request.environ.setdefault('allura.macro.att_embedded', set())
    except TypeError:
        pass
    else:
        included.add(src)
    if '://' in src or RELATIVE_OR_ABSOLUTE_URL_REGEX.match(src):
        return '<img src="%s" %s/>' % (src, ' '.join(attrs))
    else:
        return '<img src="./attachment/%s" %s/>' % (src, ' '.join(attrs))


@macro()
def img2(src=None, **kw):
    attrs = ('%s="%s"' % t for t in kw.iteritems())
    if '://' in src:
        return '<img src="%s" %s/>' % (src, ' '.join(attrs))
    else:
        return '<img src="./direct_content/%s" %s/>' % (src, ' '.join(attrs))
