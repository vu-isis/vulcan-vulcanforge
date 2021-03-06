import logging
import json
from datetime import datetime
from cStringIO import StringIO
from urllib2 import urlopen

from ming import schema as S
from ming.odm import (
    FieldProperty,
    ForeignIdProperty,
    ThreadLocalODMSession,
    state
)
from ming.utils import LazyProperty
from pylons import request, tmpl_context as c, app_globals as g

from vulcanforge.auth.schema import ACL, ACE
from vulcanforge.common.model.base import BaseMappedClass
from vulcanforge.common.model.session import main_orm_session
from vulcanforge.s3.model import File
from vulcanforge.common.util import push_config
from vulcanforge.project.exceptions import ProjectConflict
from .exceptions import RegistrationError


LOG = logging.getLogger(__name__)


class NeighborhoodFile(File):

    class __mongometa__:
        name = 'neighborhood_file'
        session = main_orm_session
        indexes = [('neighborhood_id', 'category')] + \
                  File.__mongometa__.indexes

    neighborhood_id = FieldProperty(S.ObjectId)
    category = FieldProperty(str)

    @property
    def neighborhood(self):
        return Neighborhood.query.get(_id=self.neighborhood_id)

    @property
    def default_keyname(self):
        keyname = super(NeighborhoodFile, self).default_keyname
        return '/'.join((
            'Neighborhood',
            self.neighborhood.url_prefix.strip('/'),
            keyname
        ))

    def local_url(self):
        return self.neighborhood.url() + 'icon'


class Neighborhood(BaseMappedClass):
    """Provide a grouping of related projects.

    url_prefix - location of neighborhood (may include scheme and/or host)
    css - block of CSS text to add to all neighborhood pages

    """
    class __mongometa__:
        session = main_orm_session
        name = 'neighborhood'
        polymorphic_on = 'kind'
        polymorphic_identity = 'neighborhood'
        indexes = ['name', 'url_prefix', 'allow_browse']

    _id = FieldProperty(S.ObjectId)
    name = FieldProperty(str)
    kind = FieldProperty(str, if_missing='neighborhood')
    url_prefix = FieldProperty(str)  # can be absolute or relative
    shortname_prefix = FieldProperty(str, if_missing='')
    css = FieldProperty(str, if_missing='')
    homepage = FieldProperty(str, if_missing='')
    redirect = FieldProperty(str, if_missing='')
    #projects = RelationProperty('Project')
    allow_browse = FieldProperty(bool, if_missing=True)
    site_specific_html = FieldProperty(str, if_missing='')
    project_template = FieldProperty(str, if_missing='')
    role_aliases = FieldProperty({str: str}, if_missing={})
    can_register_users = FieldProperty(bool, if_missing=False)
    enable_marketplace = FieldProperty(bool, if_missing=False)
    project_registration_enabled = FieldProperty(bool, if_missing=True)
    # extra_sidebars items should have at least name and url
    extra_sidebars = FieldProperty([{str: str}], if_missing=[])
    moderate_component_publish = FieldProperty(bool, if_missing=False)
    # moderation limits: must wait {seconds} between submission times
    moderate_component_limit_seconds = FieldProperty(int, if_missing=0)
    moderate_deletion = FieldProperty(bool, if_missing=False)
    delete_moderator_id = ForeignIdProperty('User')

    # for neighborhood project
    _default_neighborhood_apps = [
        ('neighborhood_home', 'home', 'Home'),
        ('admin', 'admin', 'Admin')
    ]

    @classmethod
    def by_prefix(cls, prefix):
        """Prefix without slashes (e.g. projects)"""
        return cls.query.get(url_prefix='/' + prefix + '/')

    @classmethod
    def get_user_neighborhood(cls):
        return cls.by_prefix('u')

    def is_user_neighboorhood(self):
        return self.url_prefix == '/u/'

    def parent_security_context(self):
        return None

    @LazyProperty
    def neighborhood_project(self):
        cls = self.neighborhood_project_cls
        return cls.query_get(
            neighborhood_id=self._id,
            shortname='--init--'
        )

    @LazyProperty
    def projects(self):
        q = {'neighborhood_id': self._id}
        return self.project_cls.query_find(q).all()

    @property
    def delete_moderator(self):
        if self.delete_moderator_id:
            user_cls = self.__class__.delete_moderator_id.related
            return user_cls.query.get(_id=self.delete_moderator_id)

    @property
    def acl(self):
        return self.neighborhood_project.acl

    def url(self):
        url = self.url_prefix
        if url.startswith('//'):
            try:
                return request.scheme + ':' + url
            except TypeError:  # pragma no cover
                return 'http:' + url
        else:
            return url

    @property
    def project_cls(self):
        from vulcanforge.project.model import Project
        return Project

    @property
    def neighborhood_project_cls(self):
        from vulcanforge.project.model import Project
        return Project

    @property
    def user_cls(self):
        from vulcanforge.auth.model import User
        return User

    @property
    def controller_class(self):
        return g.default_nbhd_controller

    @property
    def rest_controller_class(self):
        return g.default_nbhd_rest_controller

    def user_can_register(self, user=None):
        """
        Whether a user can register on a team project

        """
        return self.project_registration_enabled

    def assert_user_can_register(self, user=None):
        if not self.user_can_register(user=user):
            raise RegistrationError('User cannot register')

    def register_project(self, shortname, user=None, project_name=None,
                         user_project=False, private_project=False, apps=None,
                         tool_options=None, short_description='', **kw):
        """Register a new project in the neighborhood.  The given user will
        become the project's superuser.  If no user is specified, c.user is
        used.

        """
        from vulcanforge.project.model import ProjectFile
        if project_name is None:
            project_name = shortname
        if user is None:
            user = getattr(c, 'user', None)
        if tool_options is None:
            tool_options = {}

        default_home_text = "Welcome to your new project."
        if project_name is not None:
            default_home_text = "Welcome to the %s Project." % project_name
        if self.project_template:
            project_template = json.loads(self.project_template)
            if private_project is None and 'private' in project_template:
                private_project = project_template['private']
        else:
            project_template = {'home_text': default_home_text}

        project = self.project_cls.by_shortname(shortname)
        if project:
            raise ProjectConflict()

        if 'description' not in kw:
            kw['description'] = 'You can edit this description in the admin ' \
                                'section'
        elif not short_description:
            short_description = kw['description']

        try:
            project = self.project_cls(
                neighborhood_id=self._id,
                shortname=shortname,
                name=project_name,
                homepage_title=shortname,
                database_uri=self.project_cls.default_database_uri(shortname),
                last_updated=datetime.utcnow(),
                is_root=True,
                short_description=short_description,
                **kw
            )
            project.configure_project(
                users=[user],
                is_user_project=user_project,
                is_private_project=private_project,
                apps=apps)

            offset = int(project.ordered_mounts()[-1]['ordinal']) + 1

            if 'extra_roles' in project_template:
                from vulcanforge.project.model import ProjectRole
                for role in project_template['extra_roles']:
                    if not ProjectRole.by_name(role):
                        new = ProjectRole(project_id=project._id, name=role)
                        project.acl.append(ACE.allow(new._id, 'read'))
                        # assign role to user
                        project_role = project.project_role(user)
                        if new._id not in project_role.roles:
                            project_role.roles.append(new._id)
            if not apps and 'tools' in project_template:
                for i, tool in enumerate(project_template['tools'].keys()):
                    tool_config = project_template['tools'][tool]
                    with push_config(c, project=project, user=user):
                        app_config_options = tool_options.get(tool.lower(), {})
                        app = c.project.install_app(
                            tool,
                            mount_label=tool_config['label'],
                            mount_point=tool_config['mount_point'],
                            ordinal=i + offset,
                            acl=project_template.get('tool_acl', {}).get(
                                tool_config['mount_point']),
                            **app_config_options
                        )
                        if 'options' in tool_config:
                            app.config.options.update(tool_config['options'])
                        # app config acls will not reflect new project roles
                        # so grant the perms in project template's tool_acl
                        if 'extra_roles' in project_template:
                            acl = project_template.get('tool_acl', {}).get(
                                tool_config['mount_point'])
                            for role in acl:
                                if role in project_template['extra_roles']:
                                    pr = ProjectRole.query.get(
                                        project_id=project._id, name=role)
                                    if pr:
                                        for permission in acl[role]:
                                            ACL.upsert(
                                                app.config.acl,
                                                ACE.allow(pr._id, permission))
            if 'tool_order' in project_template:
                for i, tool in enumerate(project_template['tool_order']):
                    project.app_config(tool).options.ordinal = i
            if 'labels' in project_template:
                project.labels = project_template['labels']
            if 'home_options' in project_template:
                options = project.app_config('home').options
                for option in project_template['home_options'].keys():
                    options[option] = project_template['home_options'][option]
            if 'icon' in project_template:
                icon_file = StringIO(
                    urlopen(project_template['icon']['url']).read())
                ProjectFile.save_image(
                    project_template['icon']['filename'],
                    icon_file,
                    square=True,
                    thumbnail_size=(48, 48),
                    thumbnail_meta=dict(project_id=project._id, category='icon')
                )
        except:
            ThreadLocalODMSession.close_all()
            LOG.exception('Error registering project %s' % project)
            raise
        ThreadLocalODMSession.flush_all()
        with push_config(c, project=project, user=user):
            # have to add user to context, since this may occur inside auth
            # code for user-project reg, and c.user isn't set yet
            g.post_event('project_created')

        user.add_workspace_tab_for_project(project)
        if (
            project.neighborhood.kind == "competition" and
            project.neighborhood.monoconcilium and
            project.neighborhood.enable_marketplace
        ):
            url = "{url}home/market/browse_projects".format(
                url=project.neighborhood.url())
            user.delete_workspace_tab_to_url(url)

        return project

    def register_neighborhood_project(self, users, allow_register=False,
                                      apps=None):
        from vulcanforge.project.model import ProjectRole
        
        shortname = '--init--'
        project = self.neighborhood_project
        if project:
            raise ProjectConflict()
        name = 'Home Project for %s' % self.name
        cls = self.neighborhood_project_cls
        database_uri = cls.default_database_uri(shortname)
        project = cls(
            neighborhood_id=self._id,
            neighborhood=self,
            shortname=shortname,
            name=name,
            short_description='',
            description='You can edit this description in the admin page',
            homepage_title='# ' + name,
            database_uri=database_uri,
            last_updated=datetime.utcnow(),
            is_root=True
        )
        if apps is None:
            apps = self._default_neighborhood_apps
        try:
            project.configure_project(
                users=users,
                is_user_project=False,
                apps=apps
            )
        except:
            ThreadLocalODMSession.close_all()
            LOG.exception('Error registering project %s' % project)
            raise
        if allow_register:
            role_auth = ProjectRole.authenticated(project)
            g.security.simple_grant(project.acl, role_auth._id, 'register')
            state(project).soil()
        return project

    def bind_controller(self, controller):
        controller_attr = self.url_prefix[1:-1]
        setattr(
            controller,
            controller_attr,
            self.controller_class(self.name, self.shortname_prefix)
        )

    @property
    def icon(self):
        return NeighborhoodFile.query.get(
            neighborhood_id=self._id,
            category='icon'
        )

    def icon_url(self):
        icon = self.icon
        if icon:
            return icon.url()
        else:
            return g.resource_manager.absurl('images/project_default.png')

    @LazyProperty
    def curation_ac(self):
        for ac in self.neighborhood_project.app_configs:
            if ac.tool_name.lower() == 'curation':
                return ac
        return None

    @LazyProperty
    def can_grant_anonymous(self):
        return not g.closed_platform


class VulcanNeighborhood(Neighborhood):

    class __mongometa__:
        polymorphic_identity = 'vfneighborhood'

    kind = FieldProperty(str, if_missing='vfneighborhood')

    @property
    def project_cls(self):
        from vulcanforge.project.model import VulcanProject
        return VulcanProject


class UserNeighborhood(Neighborhood):

    class __mongometa__:
        polymorphic_identity = 'userneighborhood'

    kind = FieldProperty(str, if_missing='userneighborhood')

    @property
    def project_cls(self):
        from vulcanforge.project.model import UserProject
        return UserProject
