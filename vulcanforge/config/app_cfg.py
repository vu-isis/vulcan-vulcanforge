# -*- coding: utf-8 -*-
"""
Global configuration file for TG2-specific settings in vf.

This file complements development/deployment.ini.

Please note that **all the argument values are strings**. If you want to
convert them into boolean, for example, you should use the
:func:`paste.deploy.converters.asbool` function, as in::

    from paste.deploy.converters import asbool
    setting = asbool(global_conf.get('the_setting'))

"""
import logging
import os
import pkg_resources

from paste.deploy.converters import asbool, asint
from formencode.variabledecode import variable_decode
import jinja2
from jinja2.loaders import ChoiceLoader, FileSystemLoader, PrefixLoader
import pylons
from routes import Mapper
import tg
import tg.render
from tg.configuration import AppConfig, config
import pysolr
from boto.s3.connection import S3Connection, OrdinaryCallingFormat
from boto.exception import S3CreateError

# needed for tg.configuration to work
from vulcanforge.artifact.api import ArtifactAPI
from vulcanforge.auth.authentication_provider import (
    LocalAuthenticationProvider)
from vulcanforge.auth.security_manager import SecurityManager
from vulcanforge.auth.visibility_mode import VisibilityModeHandler
from vulcanforge.cache.redis_cache import RedisCache
from vulcanforge.common.helpers import slugify, split_subdomain
from vulcanforge.common.util.debug import (
    profile_before_call,
    profile_before_render,
    profile_after_render,
    profile_dne,
)
from vulcanforge import resources
from vulcanforge.common.util.filesystem import import_object
from vulcanforge.common.util.model import close_all_mongo_connections
from vulcanforge.config.render.jinja import PackagePathLoader
from vulcanforge.config.render.jsonify import JSONRenderer
from vulcanforge.config.render.template.filters import jsonify, timesince
from .tool_manager import ToolManager
from .context_manager import ContextManager
from vulcanforge.exchange.api import ExchangeManager
from vulcanforge.s3.auth import SwiftAuthorizer
from vulcanforge.search.solr import SolrSearch
from vulcanforge.search.util import MockSOLR
from vulcanforge.taskd.queue import RedisQueue
from vulcanforge.visualize.api import (
    ArtifactVisualizerInterface,
    UrlVisualizerInterface
)
from vulcanforge.visualize.mapping import VisualizerConfigMapper

LOG = logging.getLogger(__name__)


class ForgeConfig(AppConfig):

    static_dirs = ['vulcanforge:common/static']
    namespaced_static_dirs = {
        'artifact': ['vulcanforge:artifact/static'],
        'auth': ['vulcanforge:auth/static'],
        'dashboard': ['vulcanforge:dashboard/static'],
        'discussion': ['vulcanforge:discussion/static'],
        'exchange': ['vulcanforge:exchange/static'],
        'neighborhood': ['vulcanforge:neighborhood/static'],
        'notification': ['vulcanforge:notification/static'],
        'project': ['vulcanforge:project/static'],
        'visualize': ['vulcanforge:visualize/static'],
        'websocket': ['vulcanforge:websocket/static']
    }
    template_dirs = ['vulcanforge:common/templates']
    namespaced_template_dirs = {
        'artifact': ['vulcanforge:artifact/templates'],
        'auth': ['vulcanforge:auth/templates'],
        'dashboard': ['vulcanforge:dashboard/templates'],
        'discussion': ['vulcanforge:discussion/templates'],
        'exchange': ['vulcanforge:exchange/templates'],
        'neighborhood': ['vulcanforge:neighborhood/templates'],
        'notification': ['vulcanforge:notification/templates'],
        'project': ['vulcanforge:project/templates'],
        'visualize': ['vulcanforge:visualize/templates'],
        '_debug_util': ['vulcanforge:common/templates/_debug_util_']
    }
    vulcan_packages = ['vulcanforge']

    def __init__(self, root_controller='root'):
        AppConfig.__init__(self)
        self.root_controller = root_controller
        self.renderers = ['json', 'genshi', 'mako', 'jinja']
        self.default_renderer = 'jinja'
        self.use_sqlalchemy = False
        self.use_toscawidgets = True
        self.use_transaction_manager = False
        self.handle_status_codes = [400, 403, 404, 405, 406]
        self.call_on_shutdown = [close_all_mongo_connections]

    def after_init_config(self):
        config['pylons.strict_tmpl_context'] = True
        if not 'package' in config:
            config['package'] = self.package

    def setup_helpers_and_globals(self):
        super(ForgeConfig, self).setup_helpers_and_globals()
        self.register_packages()
        self.setup_profiling()
        self.setup_tool_manager()
        self.setup_exchange_manager()
        self.setup_resource_manager()
        self.setup_context_manager()
        self.setup_security()
        self.setup_object_store()
        self.setup_search()
        self.setup_cache()
        self.setup_task_queue()
        self.setup_visualize()
        self.setup_event_queue()
        self.setup_visibilitymode()
        self.setup_artifact()

    def register_packages(self):
        """This is a placeholder for now, but soon it will hold our extension
        framework

        """
        config['pylons.app_globals'].vulcan_packages = self.vulcan_packages

    def setup_profiling(self):
        # Profiling
        if asbool(config.get('profile_middleware', 'false')):
            self.register_hook('before_call', profile_before_call)
            self.register_hook('before_render', profile_before_render)
            self.register_hook('after_render', profile_after_render)
            config['pylons.app_globals'].profile_middleware = True
        else:
            self.register_hook('before_call', profile_dne)
            config['pylons.app_globals'].profile_middleware = False

    def setup_tool_manager(self):
        manager_path = config.get('tool_manager')
        decoded = variable_decode(config)
        tool_config = decoded.get('tools')
        if manager_path:
            cls = import_object(manager_path)
            tool_manager = cls(tool_config)
        else:
            tool_manager = ToolManager(tool_config)
        config['pylons.app_globals'].tool_manager = tool_manager

    def setup_exchange_manager(self):
        manager_path = config.get('exchange_manager')
        decoded = variable_decode(config)
        xcng_config = decoded.get('exchange', {})
        if manager_path:
            exchange_manager_cls = import_object(manager_path)
        else:
            exchange_manager_cls = ExchangeManager
        exchange_manager = exchange_manager_cls(
            xcng_config,
            config['pylons.app_globals'].tool_manager)
        config['pylons.app_globals'].exchange_manager = exchange_manager

    def setup_resource_manager(self):
        # load resource manager
        manager_path = config.get('resource_manager')
        if manager_path:
            cls = import_object(manager_path)
            resource_manager = cls(config)
        else:
            resource_manager = resources.ResourceManager(config)
        config['pylons.app_globals'].resource_manager = resource_manager

        # setup static paths
        for static_entry in self.static_dirs:
            module, dir_path = static_entry.split(':')
            os_folder = pkg_resources.resource_filename(module, dir_path)
            if os.path.exists(os_folder):
                resource_manager.register_directory('', os_folder)

        # tool static paths
        tool_manager = config['pylons.app_globals'].tool_manager
        self._register_resources_for_tools(tool_manager)

        # setup namespaced static dir paths
        for namespace, static_dirs in self.namespaced_static_dirs.items():
            for static_entry in static_dirs:
                module, dir_path = static_entry.split(':')
                os_folder = pkg_resources.resource_filename(module, dir_path)
                if os.path.exists(os_folder):
                    resource_manager.register_directory(namespace, os_folder)

        resource_manager.config_scss()

    def _register_resources_for_tools(self, tool_manager):
        resource_manager = config['pylons.app_globals'].resource_manager
        for tool_name, tool in tool_manager.tools.items():
            for static_dir in tool['app'].static_directories():
                resource_manager.register_directory(tool_name, static_dir)

    def setup_context_manager(self):
        manager_path = config.get('context_manager')
        if manager_path:
            cls = import_object(manager_path)
            context_manager = cls()
        else:
            context_manager = ContextManager()
        config['pylons.app_globals'].context_manager = context_manager

    def setup_security(self):
        manager_path = config.get('security_manager')
        if manager_path:
            cls = import_object(manager_path)
            security_manager = cls()
        else:
            security_manager = SecurityManager()
        config['pylons.app_globals'].security = security_manager

    def setup_auth(self):
        provider_path = config.get('auth_provider')
        if provider_path:
            cls = import_object(provider_path)
            auth_provider = cls()
        else:
            auth_provider = LocalAuthenticationProvider()
        config['pylons.app_globals'].auth_provider = auth_provider

    def setup_artifact(self):
        artifact_api_path = config.get('artifact_api')
        if artifact_api_path:
            cls = import_object(artifact_api_path)
            artifact_api = cls()
        else:
            artifact_api = ArtifactAPI()
        config['pylons.app_globals'].artifact = artifact_api

    def setup_search(self):
        # Setup SOLR
        solr_server = config.get('solr.server')

        if solr_server is None and config.get('solr.host'):
            solr_info = dict([
                (k, config.get("solr.%s" % k, d)) for k, d in (
                    ('host', 'localhost'),
                    ('port', 8983),
                    ('vulcan.core', 'vulcan')
                )
            ])
            solr_server = "http://%(host)s:%(port)s/solr/%(vulcan.core)s" % (
                solr_info)

        if asbool(config.get('solr.mock')):
            solr = MockSOLR()
        elif solr_server:
            solr = pysolr.Solr(solr_server)
        else:  # pragma no cover
            solr = None

        config['pylons.app_globals'].solr = solr
        config['pylons.app_globals'].search = SolrSearch(solr)

    def setup_object_store(self):
        # Setup S3 connection
        s3 = S3Connection(
            aws_access_key_id=config.get('s3.connect_string', ''),
            aws_secret_access_key=config.get('s3.password', ''),
            host=config.get('s3.ip_address', ''),
            port=asint(config['s3.port']) if config.get('s3.port') else None,
            is_secure=asbool(config.get('s3.ssl', 'false')),
            calling_format=OrdinaryCallingFormat()
        )
        s3_bucket_name = config.get('s3.bucket_name', '').rstrip('_')

        try:
            s3_bucket = s3.get_bucket(s3_bucket_name)
        except:
            try:
                s3_bucket = s3.create_bucket(s3_bucket_name)
            except S3CreateError:
                # for race conditions
                s3_bucket = s3.get_bucket(s3_bucket_name)

        s3_domain = split_subdomain(config['s3.ip_address'])
        s3_is_local = s3_domain == config['pylons.app_globals'].base_domain
        base_s3_url = '{protocol}://{host}{port_str}/'.format(
            protocol=s3.protocol,
            host=config.get('s3.public_ip', s3.host),
            port_str=':{}'.format(s3.port) if s3.port not in (80, 443) else '',
        )

        s3_serve_local = asbool(config.get('s3.serve_local', 'f'))
        s3_auth = SwiftAuthorizer()
        config['pylons.app_globals'].s3 = s3
        config['pylons.app_globals'].s3_bucket = s3_bucket
        config['pylons.app_globals'].s3_serve_local = s3_serve_local
        config['pylons.app_globals'].s3_auth = s3_auth
        config['pylons.app_globals'].s3_is_local = s3_is_local
        config['pylons.app_globals'].base_s3_url = base_s3_url

    def setup_cache(self):
        """Setup redis as a cache"""
        if config.get('redis.host'):
            if 'redis.timeout' in config:
                default_timeout = asint(config['redis.timeout'])
            else:
                default_timeout = None
            cache = RedisCache(
                host=config['redis.host'],
                port=asint(config.get('redis.port', 6379)),
                db=asint(config.get('redis.db', 0)),
                prefix=config.get('redis.prefix', ''),
                default_timeout=default_timeout
            )
        else:
            cache = None

        config['pylons.app_globals'].cache = cache

    def setup_task_queue(self):
        """This sets up a redis task queue.

        You may specify your own queue object to use, but you should probably
        override this method if it does not use redis. Either way, your queue
        should implement the API exposed on vulcanforge.taskd.queue.RedisQueue

        """
        api_path = config.get('task_queue.cls')
        if api_path:
            cls = import_object(api_path)
        else:
            cls = RedisQueue
        kwargs = {
            'host': config.get('task_queue.host', config['redis.host']),
            'port': asint(config.get('task_queue.port',
                                     config.get('redis.port', 6379))),
            'db': asint(config.get('task_queue.db',
                                   config.get('redis.db', 0)))
        }
        if config.get('task_queue.namespace'):
            kwargs['namespace'] = config['task_queue.namespace']

        task_queue = cls(config.get('task_queue.name', 'task_queue'), **kwargs)
        config['pylons.app_globals'].task_queue = task_queue

    def setup_event_queue(self):
        """This sets up a redis event queue.

        """
        api_path = config.get('event_queue.cls')
        if api_path:
            cls = import_object(api_path)
        else:
            cls = RedisQueue
        kwargs = {
            'host': config.get('event_queue.host', config['redis.host']),
            'port': asint(config.get('event_queue.port',
                                     config.get('redis.port', 6379))),
            'db': asint(config.get('event_queue.db',
                                   config.get('redis.db', 0)))
        }
        if config.get('event_queue.namespace'):
            kwargs['namespace'] = config['event_queue.namespace']

        event_queue = cls(config.get('event_queue.name', 'event_queue'),
                          **kwargs)
        config['pylons.app_globals'].event_queue = event_queue

    def setup_routes(self):
        mapper = Mapper(directory=config['pylons.paths']['controllers'],
                        always_scan=config['debug'])
        # Setup a default route for the root of object dispatch
        mapper.connect('*url', controller=self.root_controller,
                       action='routes_placeholder')
        config['routes.map'] = mapper

    def setup_template_loader(self):
        """Setup the template loader, responsible for finding the templates
        on the filesystem associated with a given path.

        One can override this by specifying the template_loader config
        parameter, but do this carefully so as not to break the default
        functionality. It may be easier to override this method instead.

        The default loader uses the jinja2.loaders.ChoiceLoader cls to
        achieve the following functionality:

        if a ":" is in the path, the left side of the path is treated as a
        package and the right as a relative path within that package to the
        template:
            g.jinja2_env.get_template('vulcanforge.auth:templates/login.html')
            >> <Template 'vulcanforge.auth:templates/login.html'>

        Otherwise, it will look for the given path in the directories specified
        on self.template_dirs, which defaults to vulcanforge/common/templates
        and the templates directory in the app.

        Finally, if the first part of the path matches a tool entry point or a
        namespace registered on self.namespaced_template_dirs, it looks for the
        file in the directories specified in that namespace.

        """
        if config.get('template_loader'):
            loader = import_object(config['template_loader'])
        else:
            loaders = [PackagePathLoader()]

            fs_paths = []
            # template directories have priority
            for tmpl_entry in self.template_dirs:
                module, dir_path = tmpl_entry.split(':')
                os_folder = pkg_resources.resource_filename(module, dir_path)
                if os.path.exists(os_folder):
                    fs_paths.append(os_folder)
            loaders.append(FileSystemLoader(fs_paths))

            # tool directory paths
            namespaces = {}
            tool_manager = config['pylons.app_globals'].tool_manager
            for tool_name, tool in tool_manager.tools.items():
                namespaces.setdefault(tool_name, []).extend(
                    tool['app'].template_directories())
            # explicit namespaced directories next
            for namespace, tmpl_dirs in self.namespaced_template_dirs.items():
                for tmpl_entry in tmpl_dirs:
                    module, dir_path = tmpl_entry.split(':')
                    os_folder = pkg_resources.resource_filename(
                        module, dir_path)
                    if os.path.exists(os_folder):
                        namespaces.setdefault(namespace, []).append(os_folder)
            # create namespaced loader
            spec = {k: FileSystemLoader(v) for k, v in namespaces.items()}
            loaders.append(PrefixLoader(spec))

            loader = ChoiceLoader(loaders)
        return loader

    def setup_jinja_renderer(self):
        jinja2_env = jinja2.Environment(
            loader=self.setup_template_loader(),
            auto_reload=self.auto_reload_templates,
            autoescape=True,
            extensions=['jinja2.ext.do', 'jinja2.ext.i18n'],
            trim_blocks=True,
            cache_size=asint(config.get('jinja.cache_size', 100))
        )
        jinja2_env.install_gettext_translations(pylons.i18n)
        jinja2_env.filters.update({
            'slugify': slugify,
            'timesince': timesince,
            'jsonify': jsonify
        })
        config['pylons.app_globals'].jinja2_env = jinja2_env
        self.render_functions.jinja = tg.render.render_jinja

    def setup_json_renderer(self):
        json_renderer = JSONRenderer(
            allow_nan=not asbool(config.get('json.strict', False)),
            sort_keys=asbool(config.get('json.sort_keys', False)),
            indent=int(config['json.indent'])
                if 'json.indent' in config else None
        )

        config['pylons.app_globals'].json_renderer = json_renderer
        self.render_functions.json = json_renderer.render_json

    def setup_visualize(self):
        if config.get('visualize.artifact_interface'):
            visualize_artifact = import_object(
                config['visualize.artifact_interface'])
        else:
            visualize_artifact = ArtifactVisualizerInterface
        if config.get('visualize.url_interface'):
            visualize_url = import_object(config['visualize.url_interface'])
        else:
            visualize_url = UrlVisualizerInterface

        if config.get('visualize.mapper'):
            visualize_mapper_cls = import_object(config['visualize.mapper'])
        else:
            visualize_mapper_cls = VisualizerConfigMapper
        visualizer_mapper = visualize_mapper_cls()

        config['pylons.app_globals'].visualize_artifact = visualize_artifact
        config['pylons.app_globals'].visualize_url = visualize_url
        config['pylons.app_globals'].visualizer_mapper = visualizer_mapper

    def setup_visibilitymode(self):
        visibility_mode = config.get('visibility_mode', 'default')
        whitelist = filter(None, config.get('visibility_holes', '').split(','))
        login_url = config.get('auth.login_url', '/auth/')
        handler = VisibilityModeHandler(visibility_mode, login_url, whitelist)
        config['pylons.app_globals'].visibility_mode_handler = handler
