# Importing this package is what triggers every language module's
# register_language() call to run — a profile module that exists on disk but isn't
# imported here contributes nothing to detection. Add one import line per language
# as it's implemented (same pattern as tools/skills/__init__.py).
#
# First iteration (fully implemented — detect, read, edit, run/build, validate,
# interpret errors): Python, JavaScript, TypeScript, Rust, Go, C, C++, C#, Ruby.
# Java, Kotlin, Swift, PHP, SQL, Shell are not yet profiled — SkyTrap can still read/
# write/shell-execute in them generically, it just doesn't have language-specific
# build/test/lint command resolution for them yet (see LanguageProfile.notes on
# each module for why "supported" means more than "the model can write the syntax").

from skytrap.core.languages import c as _c  # noqa: F401
from skytrap.core.languages import cpp as _cpp  # noqa: F401
from skytrap.core.languages import csharp as _csharp  # noqa: F401
from skytrap.core.languages import go as _go  # noqa: F401
from skytrap.core.languages import javascript as _javascript  # noqa: F401
from skytrap.core.languages import python as _python  # noqa: F401
from skytrap.core.languages import ruby as _ruby  # noqa: F401
from skytrap.core.languages import rust as _rust  # noqa: F401
from skytrap.core.languages import typescript as _typescript  # noqa: F401
from skytrap.core.languages.base import LanguageMatch, LanguageProfile, ResolvedCommands  # noqa: F401
from skytrap.core.languages.registry import all_profiles, get_profile  # noqa: F401
