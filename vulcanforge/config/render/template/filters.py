import json
import datetime

try:
    from babel.localtime import LOCALTZ
except ImportError:
    # babel < 1.0
    from babel.util import LocalTimezone as LOCALTZ
from jinja2 import evalcontextfilter
from markupsafe import Markup
from pylons import app_globals as g


@evalcontextfilter
def jsonify(eval_ctx, value):
    content = g.json_renderer.encode(value, sanitize=eval_ctx.autoescape)
    return Markup(content)


def ungettext(a, b, count):
    if count != 1:
        return b
    return a


def ugettext(a):
    return a


def timesince(d, now=None):

    """
    Takes two datetime objects and returns the time between d and now
    as a nicely formatted string, e.g. "10 minutes".  If d occurs after now,
    then "0 minutes" is returned.

    Units used are years, months, weeks, days, hours, and minutes.
    Seconds and microseconds are ignored.  Up to two adjacent units will be
    displayed.  For example, "2 weeks, 3 days" and "1 year, 3 months" are
    possible outputs, but "2 weeks, 3 hours" and "1 year, 5 days" are not.

    Adapted from http://blog.natbat.co.uk/archive/2003/Jun/14/time_since
    """
    chunks = (
        (60 * 60 * 24 * 365, lambda n: ungettext('year', 'years', n)),
        (60 * 60 * 24 * 30, lambda n: ungettext('month', 'months', n)),
        (60 * 60 * 24 * 7, lambda n: ungettext('week', 'weeks', n)),
        (60 * 60 * 24, lambda n: ungettext('day', 'days', n)),
        (60 * 60, lambda n: ungettext('hour', 'hours', n)),
        (60, lambda n: ungettext('minute', 'minutes', n))
        )
    # Convert datetime.date to datetime.datetime for comparison.
    if not isinstance(d, datetime.datetime):
        d = datetime.datetime(d.year, d.month, d.day)
    if now and not isinstance(now, datetime.datetime):
        now = datetime.datetime(now.year, now.month, now.day)

    if not now:
        if d.tzinfo:
            now = datetime.datetime.now(LOCALTZ(d))
        else:
            now = datetime.datetime.utcnow()

    # ignore microsecond part of 'd' since we removed it from 'now'
    delta = now - (d - datetime.timedelta(0, 0, d.microsecond))
    since = delta.days * 24 * 60 * 60 + delta.seconds
    if since <= 0:
        # d is in the future compared to now, stop processing.
        return u'0 ' + ugettext('minutes')
    i, seconds, count = 0, 0, 0
    name = lambda n: ungettext('minute', 'minutes', n)
    for i, (seconds, name) in enumerate(chunks):
        count = since // seconds
        if count:
            break
    s = ugettext('{number} {type}').format(number=count, type=name(count))
    if i + 1 < len(chunks):
        # Now get the second item
        seconds2, name2 = chunks[i + 1]
        count2 = (since - (seconds * count)) // seconds2
        if count2:
            s += ugettext(', {number} {type}').format(
                number=count2, type=name2(count2))
    return s
