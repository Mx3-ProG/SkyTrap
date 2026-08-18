# Importing this package is what triggers every skill's @register_tool decorator to
# run — a skill module that exists on disk but isn't imported here contributes
# nothing to the toolset. Add one import line per skill as it's implemented.

from skytrap.tools.skills.contract_review import tool as _contract_review_tool  # noqa: F401
from skytrap.tools.skills.nda_triage import tool as _nda_triage_tool  # noqa: F401
from skytrap.tools.skills.sql_queries import tool as _sql_queries_tool  # noqa: F401
from skytrap.tools.skills.docx import tool as _docx_tool  # noqa: F401
from skytrap.tools.skills.seo_audit import tool as _seo_audit_tool  # noqa: F401
from skytrap.tools.skills.dcf_model import tool as _dcf_model_tool  # noqa: F401
from skytrap.tools.skills.pitch_deck import tool as _pitch_deck_tool  # noqa: F401
