import os
import logging

import requests
from urlparse import urlparse
from pylons import tmpl_context as c, app_globals as g
from BeautifulSoup import BeautifulSoup

from vulcanforge.auth.requester import ForgeRequester
from vulcanforge.tools.wiki.model import Page


LOG = logging.getLogger(__name__)


class BrokenLinkFinder(object):

    def __init__(self, user=None):
        self.requester = ForgeRequester()
        if user:
            self.requester.set_user(user)
        self.user = user

    def make_request_for_page(self, url, page, follow_redirects=True):
        parsed = urlparse(url)
        if not parsed.netloc:
            path = parsed.path
            if not path.startswith('/'):
                path = os.path.normpath(os.path.join(page.url(), path))
            url = g.base_url + path
            if parsed.query:
                url += '?' + parsed.query
            head_func = self.requester.head
        else:
            domain = parsed.scheme + '://' + parsed.netloc
            if domain == g.base_url:
                head_func = self.requester.head
            elif self.user and domain == g.base_s3_url.rstrip('/'):
                cookies = self.user.get_swift_params()
                head_func = lambda u, **kw: requests.head(
                    u, cookies=cookies, **kw)
            else:
                head_func = requests.head
        LOG.info("Making request to %s", url)
        resp = head_func(url)
        if follow_redirects and resp.status_code == 302:
            resp = self.make_request_for_page(
                resp.headers["location"], page, False)
        return resp

    def find_broken_links_by_page(self, page):
        """Yields json with link, html str, status"""
        html = page.get_rendered_html()
        try:
            soup = BeautifulSoup(html)
        except Exception:
            raise StopIteration
        for img in soup.findAll("img"):
            src = img.get("src")
            if src:
                resp = self.make_request_for_page(src, page)
                if resp.status_code != 200:
                    yield {
                        "link": src,
                        "html": str(img),
                        "msg": "HTML Request Failure Response {}".format(
                            resp.status_code)
                    }
                elif not resp.headers["content-type"].startswith("image/"):
                    yield {
                        "link": src,
                        "html": str(img),
                        "msg": "Load Failure: Inappropriate content type"
                    }
            else:
                yield {
                    "link": None,
                    "html": str(img),
                    "msg": "Image without src attribute"
                }

        for a in soup.findAll('a'):
            href = a.get("href")
            if href:
                resp = self.make_request_for_page(href, page)
                if resp.status_code != 200:
                    yield {
                        "link": href,
                        "html": str(a),
                        "msg": "HTML Request Failure Response {}".format(
                            resp.status_code)
                    }

    def find_broken_links_by_app(self, app_config_id=None):
        """Yields triplets of link, html str, page"""
        if app_config_id is None:
            app_config_id = c.app.config._id

        query = {
            "app_config_id": app_config_id,
            "deleted": False
        }
        for page in Page.query.find(query):
            LOG.debug("Searching for links for %s", page.url())
            for err_json in self.find_broken_links_by_page(page):
                err_json["page"] = page
                yield err_json
