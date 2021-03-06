import os
import sys
import logging
from ming.utils import LazyProperty

from paste.script import command
from paste.deploy import loadapp
from paste.registry import Registry
import pylons
from tg import config

from vulcanforge.auth import credentials
from vulcanforge.auth.model import User
from vulcanforge.auth.security_manager import Credentials
from vulcanforge.common.util.model import close_all_mongo_connections


class EmptyClass(object):
    pass


class Command(command.Command):
    min_args = 0
    max_args = 1
    usage = '[<ini file>]'
    group_name = 'VulcanForge'

    def __init__(self, name, log=None):
        super(Command, self).__init__(name)
        self.log = log or logging.getLogger('vulcanforge.command')

    @LazyProperty
    def registry(self):
        return Registry()

    @LazyProperty
    def config(self):
        import tg
        return tg.config

    def run(self, args):
        result = command.Command.run(self, args)
        self.cleanup()
        return result

    def basic_setup(self):
        if self.args:
            # Configure logging
            config_file = self.args[0]

            config_name = 'config:' + config_file
            here_dir = os.getcwd()
            sys.path.insert(0, here_dir)
            try:
                # ... logging does not understand section#subsection syntax
                logging.config.fileConfig(
                    config_file.split('#')[0], disable_existing_loggers=False)
            except Exception:  # pragma no cover
                print >> sys.stderr, (
                    'Could not configure logging with config file %s' %
                    self.args[0])
            self.wsgiapp = loadapp(config_name, relative_to=here_dir)
            self.setup_globals()

            pylons.tmpl_context.user = User.anonymous()
        self.log.info('Initialize command %s with config with args %r',
                      self.__class__.__name__, self.args)

    def setup_globals(self):
        self.registry.prepare()
        self.registry.register(pylons.tmpl_context, EmptyClass())
        self.registry.register(
            pylons.app_globals, config['pylons.app_globals'])
        self.registry.register(
            credentials,
            Credentials())

    def teardown_globals(self):
        self.registry.cleanup()

    def cleanup(self):
        close_all_mongo_connections()
