from formencode import validators as fev
import ew
from ew import jinja2_ew

from vulcanforge.common import validators as V
from vulcanforge.common.widgets.forms import ForgeForm
from vulcanforge.tools.tickets.model import Bin


class BinForm(ForgeForm):

    defaults = dict(
        ew.SimpleForm.defaults,
        submit_text="Save Bin")

    class hidden_fields(ew.NameList):
        _id = jinja2_ew.HiddenField(validator=V.MingValidator(Bin),
                                    if_missing=None)

    class fields(ew.NameList):
        summary = jinja2_ew.TextField(
            label='Bin Name',
            validator=fev.UnicodeString(not_empty=True))
        terms = jinja2_ew.TextField(
            label='Search Terms',
            validator=fev.UnicodeString(not_empty=True))
