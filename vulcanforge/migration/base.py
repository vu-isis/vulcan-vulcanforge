from datetime import datetime

from ming.odm import session
from ming.odm.odmsession import ThreadLocalODMSession

from vulcanforge.migration.model import MigrationLog


class BaseMigration(object):

    def __init__(self, miglog=None):
        super(BaseMigration, self).__init__()
        if miglog is None:
            miglog = MigrationLog.upsert_from_migration(self)
        self.miglog = miglog  # persisted

    @classmethod
    def get_name(cls):
        """Used to uniquely identify a migration in the database"""
        return cls.__module__ + ':' + cls.__name__

    def is_needed(self):
        """A means of checking whether the migration script is needed for a
        given deployment. This can generally be left to return True, because
        a migration script is run only once per db, but if you need more fine-
        grained logic, override this method.

        """
        return True

    def write_output(self, msg):
        """Appends the message to the log objects output array"""
        self.miglog.output.append(msg)

    def close_sessions(self):
        session(self.miglog).flush(self.miglog)
        ThreadLocalODMSession.close_all()
        self.miglog = MigrationLog.query.get(_id=self.miglog._id)

    def full_run(self):
        """This is the method called by the MigrationRunner, but for most
        purposes, overriding the run method should be sufficient.

        """
        try:
            self.run()
        except Exception as err:
            if session(self.miglog) is None:
                self.miglog = MigrationLog.query.get(_id=self.miglog._id)
            self.write_output(repr(err))
            self.miglog.status = 'error'
            session(self.miglog).flush(self.miglog)
            raise
        else:
            self.miglog.status = 'success'
            self.miglog.ended_dt = datetime.utcnow()
            session(self.miglog).flush(self.miglog)

    def run(self):
        """Override this with your migration script logic"""
        pass
