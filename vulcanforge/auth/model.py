import logging
import urllib
import hmac
import hashlib
import time
from email import header
from datetime import timedelta, datetime
from hashlib import sha256
import os
import re
import random
import string

from markupsafe import Markup
import simplejson
import iso8601
from bson.objectid import ObjectId
import pymongo
import pyotp
from pylons import tmpl_context as c, app_globals as g
from tg import config, flash
from ming import schema as S
from ming.odm import (
    session,
    state,
    FieldProperty,
    RelationProperty,
    ForeignIdProperty
)
from ming.odm.declarative import MappedClass

from vulcanforge.common.helpers import smartencode
from vulcanforge.common.model.base import BaseMappedClass
from vulcanforge.common.model.globals import ForgeGlobals
from vulcanforge.common.model.index import SOLRIndexed
from vulcanforge.common.model.session import (
    solr_indexed_session,
    main_orm_session
)
from vulcanforge.common import helpers as h
from vulcanforge.common.util import get_client_ip, nonce, cryptographic_nonce
from vulcanforge.auth.exceptions import (
    PasswordCannotBeChangedError,
    PasswordInvalidError
)
from vulcanforge.auth.schema import ACE
from vulcanforge.common.util.model import VFRelationProperty
from vulcanforge.notification import tasks as mail_tasks
from vulcanforge.notification.util import gen_message_id

LOG = logging.getLogger(__name__)


class ApiAuthMixIn(object):

    def authenticate_request(self, path, params):
        try:
            # Validate timestamp
            timestamp = iso8601.parse_date(params['api_timestamp'])
            timestamp_utc = timestamp.replace(tzinfo=None) - \
                timestamp.utcoffset()
            if abs(datetime.utcnow() - timestamp_utc) > timedelta(minutes=10):
                return False
            # Validate signature
            api_signature = params['api_signature']
            params = sorted((k, v) for k, v in params.iteritems()
                            if k != 'api_signature')
            string_to_sign = path + '?' + smartencode(params)
            digest = hmac.new(self.secret_key, string_to_sign, hashlib.sha256)
            return digest.hexdigest() == api_signature
        except KeyError:
            return False

    def sign_request(self, path, params):
        if hasattr(params, 'items'):
            params = params.items()
        has_api_key = has_api_timestamp = has_api_signature = False
        for k, v in params:
            if k == 'api_key':
                has_api_key = True
            if k == 'api_timestamp':
                has_api_timestamp = True
            if k == 'api_signature':
                has_api_signature = True
        if not has_api_key:
            params.append(('api_key', self.api_key))
        if not has_api_timestamp:
            params.append(('api_timestamp', datetime.utcnow().isoformat()))
        if not has_api_signature:
            string_to_sign = path + '?' + smartencode(sorted(params))
            digest = hmac.new(self.secret_key, string_to_sign, hashlib.sha256)
            params.append(('api_signature', digest.hexdigest()))
        return params

    def get_capability(self, key):
        return None


class ApiToken(MappedClass, ApiAuthMixIn):

    class __mongometa__:
        name = 'api_token'
        session = main_orm_session
        unique_indexes = ['user_id']

    _id = FieldProperty(S.ObjectId)
    user_id = ForeignIdProperty('User')
    api_key = FieldProperty(str, if_missing=lambda: nonce(20))
    secret_key = FieldProperty(str, if_missing=cryptographic_nonce)

    user = RelationProperty('User')

    @classmethod
    def get(cls, api_key):
        return cls.query.get(api_key=api_key)


class ApiTicket(MappedClass, ApiAuthMixIn):
    class __mongometa__:
        name = 'api_ticket'
        session = main_orm_session
    PREFIX = 'tck'

    _id = FieldProperty(S.ObjectId)
    user_id = ForeignIdProperty('User')
    api_key = FieldProperty(
        str, if_missing=lambda: ApiTicket.PREFIX + nonce(20)
    )
    secret_key = FieldProperty(str, if_missing=cryptographic_nonce)
    expires = FieldProperty(datetime, if_missing=None)
    capabilities = FieldProperty({str: str})
    mod_date = FieldProperty(datetime, if_missing=datetime.utcnow)

    user = RelationProperty('User')

    @classmethod
    def get(cls, api_ticket):
        if not api_ticket.startswith(cls.PREFIX):
            return None
        return cls.query.get(api_key=api_ticket)

    def authenticate_request(self, path, params):
        if self.expires and datetime.utcnow() > self.expires:
            return False
        return ApiAuthMixIn.authenticate_request(self, path, params)

    def get_capability(self, key):
        return self.capabilities.get(key)


class ServiceToken(BaseMappedClass):
    """Tokens for special forge services"""

    class __mongometa__:
        name = 'service_token'
        session = main_orm_session
        indexes = ['api_key', 'user_id']

    user_id = ForeignIdProperty('User')
    api_key = FieldProperty(str, if_missing=lambda: cryptographic_nonce(128))
    expires = FieldProperty(
        datetime, if_missing=lambda: datetime.utcnow() + timedelta(days=6 * 30)
    )
    created = FieldProperty(datetime, if_missing=datetime.utcnow)

    user = RelationProperty('User')

    @classmethod
    def upsert(cls, flush=True):
        token = cls.query.get(user_id=c.user._id)
        if not token or token.is_expired():
            token = cls(user_id=c.user._id)
            if flush:
                session(cls).flush(token)
        return token

    def is_expired(self, dt=0):
        epoch = datetime.utcnow() + timedelta(seconds=dt)
        if self.expires and epoch > self.expires:
            return True
        return False

    def authenticate_request(self, environ):
        if self.is_expired():
            return False
        return True


class EmailAddress(BaseMappedClass):

    re_format = re.compile('^.* <(.*)>$')

    class __mongometa__:
        name = 'email_address'
        session = main_orm_session
        indexes = [
            ('stripped',),
            ('claimed_by_user_id',),
            ('stripped', 'confirmed'),
            ('stripped', 'claimed_by_user_id'),
            ('_id', 'stripped')
        ]

        def before_save(self):
            if not self.stripped and self.email:
                self.stripped = EmailAddress.strip(self.email)

    _id = FieldProperty(S.ObjectId)
    email = FieldProperty(str)
    stripped = FieldProperty(str, if_missing=None)
    claimed_by_user_id = FieldProperty(S.ObjectId, if_missing=None)
    confirmed = FieldProperty(bool, if_missing=False)
    nonce = FieldProperty(str)

    @classmethod
    def by_address(cls, addr, **kw):
        if addr:
            return cls.query.get(stripped=cls.strip(addr), **kw)

    def claimed_by_user(self):
        return User.query.get(_id=self.claimed_by_user_id)

    @classmethod
    def upsert(cls, addr, user_id=None):
        if user_id is None:
            user_id = c.user._id
        addr = cls.canonical(addr)
        stripped = cls.strip(addr)
        result = cls.query.get(stripped=stripped, claimed_by_user_id=user_id)
        if not result:
            result = cls(
                email=addr,
                stripped=stripped,
                claimed_by_user_id=user_id)
        return result

    @classmethod
    def strip(cls, addr):
        try:
            user, domain = addr.lower().split('@')
        except ValueError:
            return addr
        else:
            if domain in ('gmail.com', 'googlemail.com'):
                user = user.replace('.', '')
            return "{}@{}".format(user, domain)

    @classmethod
    def canonical(cls, addr):
        mo = cls.re_format.match(addr)
        if mo:
            addr = mo.group(1)
        if '@' in addr:
            user, domain = addr.split('@')
            return '%s@%s' % (user, domain.lower())
        else:
            return 'nobody@example.com'

    def confirm(self):
        self.confirmed = True
        session(self).flush(self)
        self.__class__.query.remove({
            '_id': {"$ne": self._id},
            'stripped': self.stripped
        })

    def send_verification_link(self):
        self.nonce = sha256(os.urandom(10)).hexdigest()
        LOG.info('Sending verification link to %s', self.email)
        text = '''
To verify the email address %s belongs to the user %s,
please visit the following URL:

    %s
''' % (self.email, self.claimed_by_user().username,
       g.url('/auth/verify_addr', a=self.nonce))
        LOG.info('Verification email:\n%s', text)
        mail_tasks.sendmail.post(
            destinations=[self.email],
            fromaddr=g.forgemail_return_path,
            reply_to='',
            subject='Email address verification',
            message_id=gen_message_id(),
            text=text)


class OpenId(BaseMappedClass):

    class __mongometa__:
        name = 'openid'
        session = main_orm_session

    _id = FieldProperty(str)
    claimed_by_user_id = FieldProperty(S.ObjectId, if_missing=None)
    display_identifier = FieldProperty(str)

    @classmethod
    def upsert(cls, url, display_identifier):
        result = cls.query.get(_id=url)
        if not result:
            result = cls(
                _id=url,
                display_identifier=display_identifier)
        return result

    def claimed_by_user(self):
        if self.claimed_by_user_id:
            result = User.query.get(_id=self.claimed_by_user_id)
        else:  # pragma no cover
            result = User.register(
                dict(username=None, password=None,
                     display_name=self.display_identifier,
                     open_ids=[self._id]),
                make_project=False)
            self.claimed_by_user_id = result._id
        return result


class AuthGlobals(BaseMappedClass):
    class __mongometa__:
        name = 'auth_globals'
        session = main_orm_session

    _id = FieldProperty(int)
    next_uid = FieldProperty(int, if_missing=10000)

    @classmethod
    def upsert(cls):
        r = cls.query.get()
        if r is not None:
            return r
        try:
            r = cls(_id=0)
            session(r).flush(r)
            return r
        except pymongo.errors.DuplicateKeyError:  # pragma no cover
            session(r).flush(r)
            r = cls.query.get()
            return r

    @classmethod
    def get_next_uid(cls):
        cls.upsert()
        g = cls.query.find_and_modify(
            query={}, update={'$inc': {'next_uid': 1}},
            new=True)
        return g.next_uid


class WorkspaceTab(BaseMappedClass):

    class __mongometa__:
        name = 'workspace_tab'
        session = main_orm_session
        unique_indexes = [('user_id', 'href')]
        indexes = ['user_id']

    _id = FieldProperty(S.ObjectId)
    user_id = ForeignIdProperty('User', if_missing=None)

    title = FieldProperty(str, if_missing='')
    type = FieldProperty(str, if_missing='default')
    href = FieldProperty(str, if_missing='')
    order = FieldProperty(int, if_missing=0)
    state = FieldProperty(None, if_missing=None)

    def __json__(self):
        return {
            '_id': str(self._id),
            'user_id': str(self.user_id),
            'title': self.title,
            'type': self.type,
            'href': Markup(self.href),
            'order': self.order,
            'state': self.state
        }

    def delete(self):
        # moving tabs right of the deleted one to the left by one
        tabs_to_the_right = self.__class__.query.find({
            'user_id':self.user_id,
            'order': {'$gt': self.order}
        })
        for t in tabs_to_the_right:
            t.order -= 1

        super(WorkspaceTab, self).delete()

    @classmethod
    def upsert(cls, user_id, href, **kw):
        tab = cls.query.get(user_id=user_id, href=href)
        isnew = False
        if tab:
            for key, value in kw.iteritems():
                setattr(tab, key, value)
        else:
            tab = cls(user_id=user_id, href=href, **kw)
            try:
                session(cls).flush(tab)
                isnew = True
            except pymongo.errors.DuplicateKeyError:  # pragma no cover
                session(cls).expunge(tab)
                tab = cls.query.get(user_id=user_id, href=href)
        return tab, isnew


class AppVisit(BaseMappedClass):

    class __mongometa__:
        name = 'app_visit'
        session = main_orm_session
        indexes = [('user_id', 'app_config_id')]

    _id = FieldProperty(S.ObjectId)
    user_id = ForeignIdProperty('User', if_missing=None)
    project_id = ForeignIdProperty('Project', if_missing=None)
    app_config_id = ForeignIdProperty('AppConfig', if_missing=None)

    last_visit = FieldProperty(datetime, if_missing=None)

    @classmethod
    def upsert(cls, user_id, app_config_id, project_id):
        visit = cls.query.get(user_id=user_id, app_config_id=app_config_id)
        if not visit:
            visit = cls(user_id=user_id, app_config_id=app_config_id, project_id=project_id)

        visit.last_visit = datetime.utcnow()
        visit.flush_self()

        return visit


class User(SOLRIndexed):
    """
    Vulcan's representation of individual human users.
    """
    SALT_LEN = 8

    class __mongometa__:
        name = 'user'
        """mongodb collection name"""
        session = solr_indexed_session
        polymorphic_on = 'kind'
        polymorphic_identity = 'user'
        indexes = [
            'tool_data.sfx.userid',
            'needs_password_reset',
            'password_set_at',
        ]
        unique_indexes = ['username', 'os_id']

    type_s = 'User'

    _id = FieldProperty(S.ObjectId)
    os_id = FieldProperty(int)  # used for repository auth
    kind = FieldProperty(str, if_missing='user')  # Ming ODM polymorphism field
    display_name = FieldProperty(str)
    username = FieldProperty(str)
    password = FieldProperty(str)
    open_ids = FieldProperty([str])
    public_key = FieldProperty(str, if_missing='')

    last_login = FieldProperty(datetime, if_missing=datetime.utcnow)
    has_project = FieldProperty(bool, if_missing=False)
    disabled = FieldProperty(bool, if_missing=False)
    needs_password_reset = FieldProperty(bool, if_missing=False)
    password_set_at = FieldProperty(datetime, if_missing=datetime.utcnow)
    old_password_hashes = FieldProperty([str], if_missing=[])

    """
    Don't use directly, instead use the
    :py:meth:`~vulcanforge.auth.model.User.get_pref` and
    :py:meth:`~vulcanforge.auth.model.User.set_pref` methods.
    """
    preferences = FieldProperty(dict(
        results_per_page=S.Int(if_missing=25),
        email_address=S.String(if_missing=''),
        email_format=S.String(if_missing='both'),
        autosubscribe=S.Bool(if_missing=True),
        message_emails=S.Bool(if_missing=True),
        login_landing_url=S.String(),
        two_factor_auth=S.Bool(if_missing=False)
    ))
    tool_preferences = FieldProperty({str: {str: None}})
    tool_data = FieldProperty({str: {str: None}})  # entry point: prefs dict
    # created for ui element state persistence
    state_preferences = FieldProperty(None, if_missing={})

    #workspace_tabs = FieldProperty([{str: None}], if_missing=[])
    workspace_references = FieldProperty([str], if_missing=[])
    workspace_references_last_mod = FieldProperty(
        datetime, if_missing=datetime(1978, 6, 5))

    mission = FieldProperty(str, if_missing="")
    interests = FieldProperty(str, if_missing="")
    expertise = FieldProperty(str, if_missing="")
    public = FieldProperty(bool, if_missing=True)

    content_agreed_artifacts = FieldProperty([str], if_missing=[])
    get_swift_cookies = FieldProperty(bool, if_missing=False)

    user_fields = FieldProperty({str: None})  # from signup form

    login_clients = FieldProperty({str: None})
    needs_account_verification = FieldProperty(bool, if_missing=False)
    ##################################################################

    def __json__(self):
        """
        :return: dictionary used for JSON format export
        """
        return {
            '_id': str(self._id),
            'username': self.username,
            'display_name': self.display_name,
            'url': self.url(),
            'icon_url': Markup(self.icon_url()),
            'public': self.public
        }

    @property
    def index_dict(self):
        """
        :return: dictionary used for SOLR index entry
        """
        return dict(
            _id_s=str(self._id),
            title_s='User %s (%s)' % (self.username, self.display_name),
            username_s=self.username,
            display_name_s=self.display_name,
            emails_s=', '.join(self.email_addresses),
            mission_s=self.mission,
            interests_s=self.interests,
            expertise_s=self.expertise,
            public_b=self.public,
            disabled_b=self.disabled,
            autocomplete_t=self.display_name
        )

    def is_culled(self, competition=None):
        return False

    @property
    def email_addresses(self):
        """
        :return: a list of email addresses claimed by this user
        """
        query = {'claimed_by_user_id': self._id}
        return [e.email for e in EmailAddress.query.find(query)]

    @property
    def workspace_tabs(self):
        """
        :return: A Ming (mongodb) query cursor of this user's workspace tabs
                 (bookmarks)
        :rtype: ming.Cursor
        """
        return WorkspaceTab.query.find({
            'user_id': self._id
        }).sort([
            ("order", pymongo.ASCENDING),
            ("title", pymongo.ASCENDING)
        ])

    def initialize_workspace_tabs(self, order=None, safe=False):
        # Edit own profile
        default_tabs = [{
            "title": "Edit my profile",
            "type": "default",
            "href": "{url}profile/edit_profile".format(url=self.url()),
            "state": None
        }]

        custom_tabs_from_config = simplejson.loads(
            config.get('custom_tabs', '[]'))

        return self.add_workspace_tabs(
            custom_tabs_from_config + default_tabs, order, safe=safe)

    def add_workspace_tabs(self, tab_descriptors, order=None, safe=True):
        if not order:
            last_tab = WorkspaceTab.query.find({
                'user_id': self._id
            }).sort("order", pymongo.DESCENDING).limit(1).first()
            order = last_tab.order if last_tab else 0

        for td in tab_descriptors:
            if safe:
                tab, isnew = WorkspaceTab.upsert(self._id, td.pop("href"), **td)
                if isnew:
                    tab.order = order
                    order += 1
            else:
                WorkspaceTab(order=order, user_id=self._id, **td)
                order += 1
        return order

    def add_workspace_tab_for_project(self, project, with_flash=False):
        if project.is_user_project():
            return
        tab_to_project = {
            "title": project.get_display_name(),
            "href": "{prefix}home/".format(prefix=project.url()),
        }

        self.add_workspace_tabs([tab_to_project])

        if with_flash:
            flash('A Tab to <i>{project_name}</i> was added'.format(
                project_name=project.get_display_name()))

    def delete_workspace_tab_to_url(self, url):
        tab = WorkspaceTab.query.get(user_id=self._id, href=url)
        if tab:
            tab.delete()

    def delete_workspace_tab_for_project(self, project):
        self.delete_workspace_tab_to_url("{prefix}home/".format(prefix = project.url()))

    @property
    def index_text_objects(self):
        return [self.display_name, self.expertise, self.interests]

    def get_read_roles(self):
        if self.private_project() is not None:
            return self.private_project().get_read_roles()
        else:
            return ['authenticated']

    def get_pref(self, pref_name):
        if pref_name in self.preferences:
            return self.preferences[pref_name]
        else:
            return getattr(self, pref_name)

    def set_pref(self, pref_name, pref_value):
        if pref_name in self.preferences:
            self.preferences[pref_name] = pref_value
        else:
            setattr(self, pref_name, pref_value)

    def url(self):
        """
        Get a URL for this User.
        """
        return '/u/{}/'.format(self.username)

    def icon_url(self, **gravatar_kwargs):
        user_project = self.private_project()
        if user_project and user_project.icon:
            return '/u/' + self.username + '/user_icon'

        # no avatar defined, generate based on email address
        email_address = (self.preferences.email_address or
                         "{}@vulcanforge.org".format(self.username))

        if g.use_gravatars:
            return g.gravatar(email_address, **gravatar_kwargs)
        elif user_project:
            user_project.create_avatar(email_address)
            return '/u/' + self.username + '/user_icon'
        else:
            return g.resource_manager.absurl('images/person.png')

    def landing_url(self):
        """
        Get the URL this User should be redirected to upon login.
        """
        landing_url = self.get_pref('login_landing_url')
        if not landing_url:
            if self.has_project:
                landing_url = config.get(
                    'login_landing_url', '/dashboard/')
            else:
                landing_url = config.get(
                    'getting_started_url', '/dashboard/')
        return landing_url

    def registration_neighborhood(self):
        from vulcanforge.neighborhood.model import Neighborhood
        nbhd_prefix = config.get('registration_neighborhood', 'projects')
        return Neighborhood.by_prefix(nbhd_prefix)

    def store_old_password_hash(self, hash):
        generations = int(config.get('auth.pw.generations', 10))
        self.old_password_hashes.append(hash)
        self.old_password_hashes = self.old_password_hashes[-generations:]

    @classmethod
    def active_count(cls):
        """Get the total number of active users.

        'active' for now means that a user is not 'anonymous', 'root' or
        'admin'

        :return: Number of active users
        :rtype: int
        """
        results = g.solr.search(' AND '.join((
            'type_s:(User)',
            'NOT username_s:(%s)' % ' OR '.join(('anonymous', 'root', 'admin'))
        )))
        if results is None:
            return 0
        return results.hits

    def active(self):
        return self.is_real_user() and not self.disabled

    def is_real_user(self):
        return not self.username in ('*anonymous', 'root', 'admin')

    @classmethod
    def upsert(cls, username):
        """
        Get or create a user object for the given username.

        :param username:
        :type username: basestring
        """
        u = cls.query.get(username=username)
        if u is not None:
            return u
        try:
            u = cls(username=username)
            session(u).flush(u)
        except pymongo.errors.DuplicateKeyError:
            session(u).expunge(u)
            u = cls.query.get(username=username)
        return u

    @classmethod
    def by_email_address(cls, email_address):
        """
        Lookup a User object by an email address.

        :param email_address: The email address to search for
        :type email_address: basestring
        :return: A :class:`User` object or ``None``
        """
        ea = EmailAddress.by_address(email_address)
        if ea is None:
            return None
        return ea.claimed_by_user()

    @classmethod
    def by_username(cls, name):
        if not name:
            return cls.anonymous()
        user = cls.query.get(username=name)
        return user

    @classmethod
    def by_display_name(cls, name):
        name_regex = re.compile('(?i)%s' % re.escape(name))
        users = cls.query.find(dict(
            display_name=name_regex)).sort('username').all()
        return users

    @classmethod
    def by_id(cls, _id):
        if isinstance(_id, basestring):
            _id = ObjectId(_id)
        return cls.query.get(_id=_id)

    def get_tool_data(self, tool, key, default=None):
        return self.tool_data.get(tool, {}).get(key, None)

    def set_tool_data(self, tool, **kw):
        d = self.tool_data.setdefault(tool, {})
        d.update(kw)
        state(self).soil()

    def address_object(self, addr):
        return EmailAddress.query.get(
            stripped=EmailAddress.strip(addr),
            claimed_by_user_id=self._id
        )

    def openid_object(self, oid):
        return OpenId.query.get(_id=oid, claimed_by_user_id=self._id)

    def claim_openid(self, oid_url):
        oid_obj = OpenId.upsert(oid_url, self.get_pref('display_name'))
        oid_obj.claimed_by_user_id = self._id
        if oid_url in self.open_ids:
            return
        self.open_ids.append(oid_url)

    def claim_address(self, email_address, confirmed=False, is_primary=False):
        addr = EmailAddress.canonical(email_address)
        email_addr = EmailAddress.upsert(addr, self._id)
        if confirmed:
            email_addr.confirm()
            if is_primary:
                self.set_pref('email_address', addr)

    @classmethod
    def register(cls, doc, make_project=True, neighborhood=None):
        """
        Register a new user.

        :return: instance of ``cls`` (:class:`User` or a subclass thereof)
        """
        from vulcanforge.neighborhood.model import Neighborhood
        user = g.auth_provider.register_user(doc, neighborhood)
        if user and 'display_name' in doc:
            user.set_pref('display_name', doc['display_name'])
        if user:
            user.os_id = ForgeGlobals.inc_user_counter()
            user.initialize_workspace_tabs()
        if user and make_project:
            n = Neighborhood.query.get(name='Users')
            p = n.register_project(
                'u/' + user.username, user=user, user_project=True)
            # Allow for special user-only tools
            p._extra_tool_status = ['user']

        return user

    def private_project(self):
        from vulcanforge.project.model import Project
        try:
            shortname = 'u/%s' % self.username
            return Project.query_get(shortname=shortname, deleted=False)
        except S.Invalid:
            LOG.exception(
                'Error retrieving private_project for %s', self.username)
            return None

    @property
    def script_name(self):
        return '/u/' + self.username + '/'

    def get_role_ids(self):
        """
        Find all of the User's ProjectRole IDs
        """
        role_cache = g.security.credentials.user_roles(user_id=self._id)
        return list(role_cache.reaching_ids_set)

    def make_public(self):
        from vulcanforge.project.model import ProjectRole

        user_project = self.private_project()
        read_roles = [
            ProjectRole.by_name('*authenticated', user_project)._id,
            ProjectRole.by_name('*anonymous', user_project)._id
        ]

        for ace in user_project.acl:
            if ace.role_id in read_roles and ace.access == ACE.ALLOW\
            and ace.permission == 'read':
                break
        else:
            user_project.acl.append(ACE.allow(read_roles[0], 'read'))
        self.public = True

    def make_private(self):
        from vulcanforge.project.model import ProjectRole

        user_project = self.private_project()
        read_roles = [
            ProjectRole.by_name('*authenticated', user_project)._id,
            ProjectRole.by_name('*anonymous', user_project)._id
        ]
        user_project.acl = [
            ace for ace in user_project.acl
            if not (ace.role_id in read_roles and ace.access == ACE.ALLOW)
        ]
        self.public = False

    def get_roles(self):
        """
        Find all of the User's ProjectRoles
        """
        from vulcanforge.project.model import ProjectRole
        return ProjectRole.query.find({'_id': {'$in': self.get_role_ids()}})

    def my_projects(self, nbhd=None, nbhd_id=None):
        """
        Find the projects for which this user has a named role.
        """
        reaching_roles = self.get_roles()
        named_roles = [r for r in reaching_roles if r.name]
        seen_project_ids = set()
        if nbhd_id is None:
            if nbhd is None:
                nbhd = self.registration_neighborhood()
            nbhd_id = nbhd._id
        for r in named_roles:
            if r.project.neighborhood_id != nbhd_id:
                seen_project_ids.add(r.project_id)
                continue
            if r.project_id in seen_project_ids:
                continue
            yield r.project
            seen_project_ids.add(r.project_id)

    def set_password(self, new_password, as_admin=False, set_time=True,
                     current=None):
        """
        Change this User's password.

        :param new_password: plaintext password
        :type new_password: basestring
        :param as_admin: passed to the authentication provider
                         (for instance as required by LDAP)
        :type as_admin: bool
        :param set_time: update the ``password_set_at`` and
                         ``needs_password_reset`` fields of this User
        :param current: plaintext current password
        """
        # the following performs the auth.pw.min_length, auth.pw.max_length,
        # complexity and similarity (auth.pw.min_levenshtein) checks
        from vulcanforge.auth.validators import validate_password
        validation = validate_password(new_password, current)
        if validation != 'success':
            raise PasswordInvalidError(validation)
        # the following performs the minimum password lifetime check
        if self.password_set_at is None:
            self.password_set_at = datetime.utcnow()
        else:
            min_hours = int(config.get('auth.pw.min_lifetime.hours', 24))
            age = datetime.utcnow() - self.password_set_at
            # min_check addresses newly created users
            min_check = age > timedelta(seconds=5)
            if min_check and age < timedelta(hours=min_hours):
                msg = "Password minimum lifetime not yet exceeded."
                raise PasswordCannotBeChangedError(msg)
        # the following performs the auth.pw.generations check
        result = g.auth_provider.set_password(
            self, self.password, new_password, as_admin)
        if set_time:
            self.password_set_at = datetime.utcnow()
            self.needs_password_reset = False
        return result

    def get_workspace_tabs(self):
        """
        :return: JSON string of this User's workspace tabs (bookmarks)
        :rtype: str | unicode
        """
        return simplejson.dumps([t.__json__() for t in self.workspace_tabs])

    def get_workspace_references(self):
        references = []
        for ref_id in self.workspace_references:
            ref = self._make_workspace_reference(ref_id)
            if ref:
                references.append(ref)

        last_mod = self.workspace_references_last_mod
        result = {
            'contents': references,
            'last_mod': h.stringify_datetime(last_mod)
        }

        return result

    def _make_workspace_reference(self, ref_id):
        from vulcanforge.artifact.widgets import short_artifact_link_data
        artifact = g.artifact.get_artifact_by_index_id(ref_id)
        if artifact:
            return short_artifact_link_data(artifact)

    @classmethod
    def anonymous(cls):
        """
        Get the *anonymous* user
        """
        return User.query.get(_id=None)

    @property
    def is_anonymous(self):
        """
        Test if this User is the *anonymous* user
        """
        return self._id is None

    def email_address_header(self):
        email_header = header.Header()
        email_header.append(u'"%s" ' % self.get_pref('display_name'))
        email_header.append(u'<%s>' % self.get_email_address())
        return email_header

    def get_email_address(self):
        """
        Get this User's primary email address

        :rtype: str | unicode
        """
        from_pref = self.get_pref('email_address')
        if from_pref:
            return from_pref
        if self.email_addresses:
            return self.email_addresses[0]

    @property
    def registration_time(self):
        if self._id:
            reg_time = time.mktime(self._id.generation_time.timetuple())
        else:
            reg_time = 1.0
        return reg_time

    def get_profile_info(self):
        return {
            "fullName": self.display_name,
            "profileImage": self.icon_url(),
            "username": self.username,
            "mission": self.mission,
            "interests": self.interests,
            "expertise": self.expertise,
            "userSince": h.ago_ts(self.registration_time),
            "projects": [p.shortname for p in self.my_projects()
                         if p and p.is_real()],
            "user_fields": self.user_fields
        }

    def get_profile_info_authed(self, auth_user):
        projects = [p.shortname for p in self.my_projects()
                    if p.is_real() and not p.deleted and
                    g.security.has_access(p, "read", auth_user)]
        return {
            "fullName": self.display_name,
            "profileImage": self.icon_url(),
            "username": self.username,
            "mission": self.mission,
            "interests": self.interests,
            "expertise": self.expertise,
            "userSince": h.ago_ts(self.registration_time),
            "projects": projects,
            "user_fields": {}
        }

    def delete_account(self):
        """
        Disables user and resigns from all projects
        """
        for project in self.my_projects():
            if project.neighborhood.name != 'Users':
                if len(project.users()) == 1:
                    project.delete_project()
                project.user_leave_project(self)
        self.disabled = True


class PasswordResetToken(BaseMappedClass):

    class __mongometa__:
        session = main_orm_session
        name = 'password_reset_token'
        indexes = [('user_id',), ('nonce',)]

    _id = FieldProperty(S.ObjectId)
    user_id = ForeignIdProperty('User')
    email = FieldProperty(str)
    nonce = FieldProperty(str, if_missing='')
    expiry_date = FieldProperty(datetime, if_missing=datetime.utcnow)

    _user = None

    @property
    def user(self):
        if not self._user:
            self._user = User.query.get(_id=self.user_id)
        return self._user

    @property
    def is_valid(self):
        expired = self.expiry_date < datetime.utcnow()
        return not expired and self.nonce and self.user

    def reset_url(self):
        return g.url('/auth/password_reset', token=self.nonce)

    def send_email(self):
        if not self.is_valid:
            LOG.warn('Invalid password reset: %s', self)
            self.delete()
            return False
        LOG.info('Sending password reset link to %s (%s)',
                 self.user.username, self.email)
        template = g.jinja2_env.get_template('auth/mail/password_reset.txt')
        text = template.render({
            'name': self.user.display_name,
            'username': self.user.username,
            'url': self.reset_url(),
            'forge_name': config.get('forge_name')
        })
        LOG.info('Password reset email:\n%s', text)
        mail_tasks.sendmail.post(
            destinations=[self.email],
            fromaddr=g.forgemail_return_path,
            reply_to='',
            subject='Reset your {} password'.format(
                config.get('forge_name', 'forge')),
            message_id=gen_message_id(),
            text=text)
        return True


class UserRegistrationToken(BaseMappedClass):

    class __mongometa__:
        session = main_orm_session
        name = 'user_registration'
        indexes = ['email_address', 'nonce']

    _id = FieldProperty(S.ObjectId)
    email = FieldProperty(str)
    name = FieldProperty(str)  # User's name
    username = FieldProperty(str, if_missing=None)
    user_fields = FieldProperty({str: None})
    registration_url = FieldProperty(str, if_missing=None)

    nonce = FieldProperty(str, if_missing='')
    expiry_date = FieldProperty(datetime, if_missing=datetime.utcnow)
    approved_already = FieldProperty(bool, if_missing=False)

    # adds user to project on registration
    project_id = ForeignIdProperty('Project', if_missing=None)
    project = VFRelationProperty('Project', via="project_id")
    project_role_id = ForeignIdProperty('ProjectRole', if_missing=None)
    project_role = VFRelationProperty('ProjectRole', via="project_role_id")

    @property
    def email_subject(self):
        return "{} User Registration".format(
            config.get('forge_name', 'Forge'))

    @property
    def is_valid(self):
        expired = self.expiry_date < datetime.utcnow()
        return bool(not expired and self.nonce)

    @property
    def full_registration_url(self):
        base_url = self.registration_url or '/auth/register'
        return g.url(base_url, token=self.nonce)

    @property
    def email_text(self):
        template = g.jinja2_env.get_template('auth/mail/user_registration.txt')
        return template.render({
            'url': self.full_registration_url,
            'name': self.name,
            'forge_name': config.get('forge_name')
        })

    def send(self):
        text = self.email_text
        mail_tasks.sendmail.post(
            fromaddr=g.forgemail_return_path,
            destinations=[self.email],
            reply_to='',
            subject=self.email_subject,
            message_id=gen_message_id(),
            text=text
        )
        LOG.info('User registration email:\n{}'.format(text))


class EmailChangeToken(BaseMappedClass):

    class __mongometa__:
        session = main_orm_session
        name = 'email_change_notification'
        indexes = ['email_address', 'nonce']

    _id = FieldProperty(S.ObjectId)
    old_email = FieldProperty(str)
    new_email = FieldProperty(str)
    user_id = ForeignIdProperty(User, if_missing=lambda: c.user._id)
    nonce = FieldProperty(str, if_missing=lambda: cryptographic_nonce(128))
    created_date = FieldProperty(datetime,
                                 if_missing=lambda: datetime.utcnow())
    expiry_date = FieldProperty(
        datetime, if_missing=lambda: datetime.utcnow() + timedelta(days=2))

    @property
    def user(self):
        return User.query.get(_id=self.user_id)

    @property
    def is_valid(self):
        expired = self.expiry_date < datetime.utcnow()
        return not expired and self.nonce and self.user

    def reset_url(self):
        return g.url('/auth/cancel_email_modification', token=self.nonce)

    def send_email(self):
        if not self.is_valid:
            LOG.warn('Invalid email change reversion: %s', self)
            self.delete()
            return False
        LOG.info('Sending email change reversion link to %s (%s)',
                 self.user.username, self.old_email)
        template = g.jinja2_env.get_template(
            'auth/mail/email_change_reversion.txt')
        text = template.render({
            'username': self.user.username,
            'old_email': self.old_email,
            'new_email': self.new_email,
            'url': self.reset_url(),
            'forge_name': config.get('forge_name', "Forge")
        })
        LOG.info('Email change email:\n%s', text)
        mail_tasks.sendmail.post(
            destinations=[self.old_email],
            fromaddr=g.forgemail_return_path,
            reply_to='',
            subject='{} Email Modification'.format(
                config.get('forge_name', 'Forge')),
            message_id=gen_message_id(),
            text=text)
        return True


class LoginVerificationToken(BaseMappedClass):

    class __mongometa__:
        session = main_orm_session
        name = 'login_verification_token'
        indexes = [('user_id',), ('cookie',)]

    _id = FieldProperty(S.ObjectId)
    user_id = ForeignIdProperty('User')
    email = FieldProperty(str)
    client_ip = FieldProperty(str)
    nonce = FieldProperty(str, if_missing='')
    cookie = FieldProperty(str, if_missing='')
    destination = FieldProperty(str, if_missing='')
    reason = FieldProperty(str, if_missing='')
    expiry_date = FieldProperty(datetime, if_missing=datetime.utcnow)

    _user = None

    @property
    def user(self):
        if not self._user:
            self._user = User.query.get(_id=self.user_id)
        return self._user

    @property
    def is_valid(self):
        expired = self.expiry_date < datetime.utcnow()
        return not expired and self.nonce and self.user

    def generate_nonce(self, length):
        return ''.join(random.SystemRandom().choice(string.digits)
                       for _ in range(length))

    def send_email(self):
        if not self.is_valid:
            LOG.warn('Invalid login verification: %s', self)
            self.delete()
            return False
        LOG.info('Sending login verification to %s (%s) for %s',
                 self.user.username, self.email, self.reason)
        tname = "unknown_ip" if self.reason == "clientip" else "verify_acct"
        tbase = "vulcanforge.auth:templates/mail/"
        template = g.jinja2_env.get_template(tbase + tname + ".html")
        reason =  "Access" if self.reason == "clientip" else "Account"
        subject = "{} {} Verification".format(g.forge_name, reason)
        tdict = dict(user=self.user.display_name,
                     forge_name=g.forge_name,
                     nonce=self.nonce)
        if self.reason == "clientip":
            tdict['ip'] = self.client_ip
        text = template.render(tdict)
        LOG.info('Login verification email:\n%s', text)
        mail_tasks.sendmail.post(
            destinations=[self.email],
            fromaddr=g.forgemail_return_path,
            reply_to='',
            subject=subject,
            message_id=gen_message_id(),
            text='',
            title_html='<h1>' + subject + '</h1>',
            html_text=text)
        return True


class TwoFactorAuthenticationToken(BaseMappedClass):

    class __mongometa__:
        session = main_orm_session
        name = 'two_factor_authentication_token'
        indexes = [('user_id',), ('cookie',)]

    _id = FieldProperty(S.ObjectId)
    user_id = ForeignIdProperty('User')
    client_ip = FieldProperty(str)
    cookie = FieldProperty(str, if_missing='')
    destination = FieldProperty(str, if_missing='')
    expiry_date = FieldProperty(datetime, if_missing=datetime.utcnow)

    _user = None

    @property
    def user(self):
        if not self._user:
            self._user = User.query.get(_id=self.user_id)
        return self._user

    @property
    def is_valid(self):
        expired = self.expiry_date < datetime.utcnow()
        return not expired and self.cookie and self.user

    def verify(self, code):
        if self.user:
            conf = self.user.user_fields.get('totp', None)
            if conf:
                t = pyotp.TOTP(conf)
                return t.verify(code)
        return False


class UsersDenied(BaseMappedClass):

    class __mongometa__:
        session = main_orm_session
        name = 'users_denied'

    _id = FieldProperty(S.ObjectId)
    email = FieldProperty(str)


class FailedLogin(BaseMappedClass):

    class __mongometa__:
        session = main_orm_session
        name = 'failed_login'
        indexes = ['client', 'username', 'timestamp']

    _id = FieldProperty(S.ObjectId)
    client = FieldProperty(str)
    username = FieldProperty(str, if_missing=None)
    timestamp = FieldProperty(datetime, if_missing=datetime.utcnow)

    @classmethod
    def is_locked(cls, request):
        """Determine whether logins for current client are locked"""
        max_fails = int(config.get('login_lock.num', 0))
        if not max_fails:
            return False

        client = cls.get_client_from_request(request)
        username = request.params.get('username')

        if client and cls.num_recent(client, username) >= max_fails:
            return True

        return False

    @staticmethod
    def get_client_from_request(request):
        client = get_client_ip(request)
        return client

    @classmethod
    def num_recent(cls, client, username):
        cutoff = datetime.utcnow() - timedelta(minutes=int(
            config.get('login_lock.interval', 0)))
        return cls.query.find({
            'client': client,
            'username': username,
            'timestamp': {'$gt': cutoff}
        }).count()

    @classmethod
    def from_request(cls, request):
        return cls(
            client=cls.get_client_from_request(request),
            username=request.params.get('username')
        )


class ToolsInfo(BaseMappedClass):

    class __mongometa__:
        session = main_orm_session
        name = 'tools_info'
        indexes = ['timestamp', 'user_id']

    _id = FieldProperty(S.ObjectId)
    timestamp = FieldProperty(datetime, if_missing=datetime.utcnow)
    user_id = ForeignIdProperty('User')
    info = FieldProperty({str: None})

    @property
    def user(self):
        return User.query.get(_id=self.user_id)
