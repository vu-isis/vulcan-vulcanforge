import json
import os
import random
import string
import urllib
from urlparse import urlparse

from pylons import app_globals as g
from vulcanforge.common.helpers import slugify, urlquote

from vulcanforge.resources.widgets import Widget, JSLink, CSSLink, JSScript


class BaseContentWidget(Widget):

    def resources(self):
        yield JSLink('js/lib/jquery/jquery.1.7.2.min.js', scope="forge")
        yield JSLink('js/lib/jquery/jquery-ui.1.10.3.js', scope="forge")
        yield JSLink('visualize/js/visualize.js', scope="forge")

        yield CSSLink('css/core.scss')
        yield CSSLink('css/hilite.css')
        yield CSSLink('theme/css/theme.scss')
        yield CSSLink('visualize/css/visualize.css')

    def needs_credentials(self, value):
        needs_creds = False
        parsed = urlparse(value)
        base_url = '{}://{}/'.format(parsed.scheme, parsed.netloc)
        if not g.s3_serve_local and g.base_s3_url == base_url:
            needs_creds = True
        return needs_creds

    def display(self, value, uid=None, **kw):
        if uid is None:
            uid = ''.join(random.sample(string.ascii_uppercase, 8))
        with_credentials = self.needs_credentials(value)
        return super(BaseContentWidget, self).display(
            value=value, uid=uid, with_credentials=with_credentials, **kw)


class ProcessingContentWidget(BaseContentWidget):
    """For visualizers that implement preprocessing hooks"""
    def resources(self):
        for r in super(ProcessingContentWidget, self).resources():
            yield r
        yield JSScript('''
        $(VIS).on("initConfig", function(e, config){
            config.loadingImg = "%s";
        });
        ''' % g.resource_manager.absurl("images/PleaseWait.gif"))


class IFrame(Widget):
    """
    Simple iframe for embedding a visualizer

    """
    template = 'visualize/widgets/iframe.html'
    defaults = dict(
        Widget.defaults,
        no_iframe_msg=(
            'Please install iframes in your browser to view this content'),
        fs_url=None,
        src=None,
        new_window_button=False,
        height="700px"
    )

    def get_query(self, value, visualizer, extra_params=None):
        from base import BaseProcessingVisualizer
        query = visualizer.get_query_for_url(value)

        if extra_params:
            # Special case visualizing files from the exchange that are derived
            if isinstance(visualizer, BaseProcessingVisualizer) and\
               extra_params.has_key('resource_url') and\
               query['resource_url'].startswith('/s3_proxy'):

                extra_params.pop('resource_url')

            query.update(extra_params)
        return query

    def get_full_urls(self, value, visualizer, extra_params=None):
        # If extra_params contains node_id make sure to add it to the resource_url
        node_id = None
        if extra_params is not None and extra_params.has_key('node_id'):
            node_id = extra_params.pop('node_id')

        query = self.get_query(value, visualizer, extra_params)
        if node_id is not None:
            resource_url = query.pop("resource_url")
            if 'node_id' not in resource_url:
                if "?" in resource_url:
                    resource_url += "&node_id={}".format(node_id)
                else:
                    resource_url += "?node_id={}".format(node_id)
            query.update({'resource_url':resource_url})

        src_url = visualizer.src_url + '?' + urllib.urlencode(query)
        fs_query = {
            "resource_url": query.pop("resource_url"),
            "iframe_query": urlquote(urllib.urlencode(query))
        }
        fs_url = visualizer.fs_url + '?' + urllib.urlencode(fs_query)
        return src_url, fs_url

    def display(self, value, visualizer, extra_params=None,
                new_window_button=False, height="700px", **kwargs):
        src_url, fs_url = self.get_full_urls(value, visualizer, extra_params)
        kwargs['src'] = src_url
        if new_window_button:
            kwargs['fs_url'] = fs_url
        if height and height.isdigit():
            height += "px"
        return Widget.display(self, height=height, **kwargs)


class ArtifactIFrame(IFrame):
    """Renders iframe given an artifact"""

    def get_query(self, value, visualizer, extra_params=None):
        from base import BaseProcessingVisualizer
        query = visualizer.get_query_for_artifact(value)

        if extra_params:
            # Special case visualizing files from the exchange that are derived
            if isinstance(visualizer, BaseProcessingVisualizer) and\
               extra_params.has_key('resource_url') and\
               query['resource_url'].startswith('/s3_proxy'):

                extra_params.pop('resource_url')

            query.update(extra_params)
        return query


class TabbedVisualizers(Widget):
    template = 'visualize/widgets/tabbedvisualizers.html'
    js_template = '''
    $(function(){
        $("#visualizerTabs_{{uid}}").tabbedVisualizer({
            visualizerSpecs: JSON.parse('{{ visualizer_specs }}'),
            downloadUrl: "{{ download_url }}",
            filename: "{{ filename }}",
            height: "{{ height }}"
        });
    });
    '''

    defaults = dict(
        Widget.defaults,
        filename='',
        download_url='',
        new_window_button=True,
        height="700px"
    )

    def resources(self):
        yield JSLink('visualize/js/tabbed_visualizer.js')

    def display(self, visualizer_specs, uid=None, height="700px", **kw):
        """
        @param visualizer_specs list of dictoniaries:
        [{
            "name": str name of visualizer,
            "iframe_url": str from Visualizer.render_url or
                Visualizer.render_artifact
            "fullscreen_url": str optional url for fullscreen window button
            "active": bool optional default False
        }, ...]

        """
        if uid is None:
            uid = ''.join(random.sample(string.ascii_lowercase, 8))
        visualizer_specs = json.dumps(visualizer_specs).replace('\\', '\\\\')
        if height and height.isdigit():
            height += "px"
        return super(TabbedVisualizers, self).display(
            visualizer_specs=visualizer_specs, uid=uid, height=height, **kw)


class UrlDiff(Widget):
    template = 'visualize/widgets/diff.html'
    defaults = dict(
        Widget.defaults,
        no_iframe_msg=(
            'Please install iframes in your browser to view this content')
    )

    def get_queries(self, value, value2, visualizer, extra_params=None):
        query1 = visualizer.get_query_for_url(value)
        query2 = visualizer.get_query_for_url(value2)
        if extra_params:
            query1.update(extra_params)
            query2.update(extra_params)
        return query1, query2

    def get_src_urls(self, value, value2, visualizer, extra_params=None):
        query1, query2 = self.get_queries(
            value, value2, visualizer, extra_params)
        base_url = visualizer.src_url + '?'
        url1 = base_url + urllib.urlencode(query1)
        url2 = base_url + urllib.urlencode(query2)
        return url1, url2

    def get_filename_from_value(self, value):
        parsed = urlparse(value)
        return os.path.basename(parsed.path)

    def display(self, value, value2, visualizer, extra_params=None,
                filename1=None, filename2=None, **kwargs):
        url1, url2 = self.get_src_urls(value, value2, visualizer, extra_params)
        if filename1 is None:
            filename1 = self.get_filename_from_value(value)
        if filename2 is None:
            filename2 = self.get_filename_from_value(value2)
        return super(UrlDiff, self).display(
            url1=url1,
            filename1=filename1,
            url2=url2,
            filename2=filename2
        )


class ArtifactDiff(UrlDiff):
    def get_queries(self, value, value2, visualizer, extra_params=None):
        query1 = visualizer.get_query_for_artifact(value)
        query2 = visualizer.get_query_for_artifact(value2)
        if extra_params:
            query1.update(extra_params)
            query2.update(extra_params)
        return query1, query2

    def get_filename_from_value(self, value):
        return super(ArtifactDiff, self).get_filename_from_value(value.url())


class TabbedDiffs(Widget):
    template = 'visualize/widgets/tabbed_diffs.html'
    js_template = '''
    $(function(){
        $('#visualizerDiffTabs_{{ uid }}').tabs();
    });
    '''

    def display(self, diff_specs, uid=None, **kw):
        """
        @param diff_specs: list of dictoniaries:
        [{
            "name": str name of visualizer,
            "content": str html content of diff
            "slug": str optional default sluggified name
        }, ...]

        """
        for spec in diff_specs:
            spec.setdefault('slug', slugify(spec["name"]))
        if uid is None:
            uid = ''.join(random.sample(string.ascii_lowercase, 8))
        return super(TabbedDiffs, self).display(
            diff_specs=diff_specs, uid=uid, **kw)
