#!/usr/bin/env python3
import ast
from collections import namedtuple
from contextlib import suppress
from functools import lru_cache, partial
import itertools
import logging

import attr
import pycodestyle


__version__ = '17.2.1'

LOG = logging.getLogger('flake8.bugbear')


@attr.s(hash=False)
class BugBearChecker:
    name = 'flake8-bugbear'
    version = __version__

    tree = attr.ib(default=None)
    filename = attr.ib(default='(none)')
    lines = attr.ib(default=None)
    max_line_length = attr.ib(default=79)
    visitor = attr.ib(default=attr.Factory(lambda: BugBearVisitor))
    options = attr.ib(default=None)

    def run(self):
        if not self.tree or not self.lines:
            self.load_file()
        visitor = self.visitor(
            filename=self.filename,
            lines=self.lines,
        )
        visitor.visit(self.tree)
        for e in itertools.chain(visitor.errors, self.gen_line_based_checks()):
            if pycodestyle.noqa(self.lines[e.lineno - 1]):
                continue

            if self.should_warn(e.message[:4]):
                yield self.adapt_error(e)

    def gen_line_based_checks(self):

        return
        yield

    @classmethod
    def adapt_error(cls, e):
        """Adapts the extended error namedtuple to be compatible with Flake8."""
        try:
            formatted_message = e.message.format(*e.vars)
        except (IndexError, KeyError):
            formatted_message = e.message.format(vars=e.vars[0] if len(e.vars) == 1 else e.vars)
        return e._replace(message=formatted_message)[:4]

    def load_file(self):
        """Loads the file in a way that auto-detects source encoding and deals
        with broken terminal encodings for stdin.

        Stolen from flake8_import_order because it's good.
        """

        if self.filename in ("stdin", "-", None):
            self.filename = "stdin"
            self.lines = pycodestyle.stdin_get_value().splitlines(True)
        else:
            self.lines = pycodestyle.readlines(self.filename)

        if not self.tree:
            self.tree = ast.parse("".join(self.lines))

    @classmethod
    def add_options(cls, optmanager):
        """Informs flake8 to ignore B9xx by default."""
        optmanager.extend_default_ignore(disabled_by_default)

    @lru_cache()
    def should_warn(self, code):
        """Returns `True` if Bugbear should emit a particular warning.

        flake8 overrides default ignores when the user specifies
        `ignore = ` in configuration.  This is problematic because it means
        specifying anything in `ignore = ` implicitly enables all optional
        warnings.  This function is a workaround for this behavior.
        """
        if self.options is None:
            LOG.info("Options not provided to Bugbear, optional warning %s selected.", code)
            return True

        for i in self.options.select:
            if code.startswith(i):
                return True

        for i in self.options.ignore + self.options.extend_ignore:
            if code.startswith(i):
                return False

        return code not in disabled_by_default


@attr.s
class BugBearVisitor(ast.NodeVisitor):
    filename = attr.ib()
    lines = attr.ib()
    node_stack = attr.ib(default=attr.Factory(list))
    node_window = attr.ib(default=attr.Factory(list))
    errors = attr.ib(default=attr.Factory(list))
    futures = attr.ib(default=attr.Factory(set))

    NODE_WINDOW_SIZE = 4

    if False:
        # Useful for tracing what the hell is going on.

        def __getattr__(self, name):
            print(name)
            return self.__getattribute__(name)

    def visit(self, node):
        self.node_stack.append(node)
        self.node_window.append(node)
        self.node_window = self.node_window[-self.NODE_WINDOW_SIZE:]
        super().visit(node)
        self.node_stack.pop()

    def visit_ExceptHandler(self, node):
        self.generic_visit(node)

    def visit_UAdd(self, node):
        self.generic_visit(node)

    def visit_Call(self, node):
        self.generic_visit(node)

    def visit_Attribute(self, node):
        self.generic_visit(node)

    def visit_Assign(self, node):
        self.generic_visit(node)

    def visit_For(self, node):
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.generic_visit(node)

    def compose_call_path(self, node):
        if isinstance(node, ast.Attribute):
            yield from self.compose_call_path(node.value)
            yield node.attr
        elif isinstance(node, ast.Name):
            yield node.id

    def check_for_b005(self, node):
        return

    def check_for_b006(self, node):
        return

    def check_for_b007(self, node):
        return

    def check_for_b901(self, node):
        return

    def check_for_b902(self, node):
        return

    def check_for_b903(self, node):
        return


@attr.s
class NameFinder(ast.NodeVisitor):
    """Finds a name within a tree of nodes."""
    names = attr.ib(default=attr.Factory(dict))

    def visit_Name(self, node):
        self.names.setdefault(node.id, []).append(node)

    def visit(self, node):
        """Like super-visit but doesn't invoke visit_Name."""
        for child in ast.iter_child_nodes(node):
            self.visit(child)


def _is_identifier(arg):
    if hasattr(ast, 'arg'):
        return isinstance(arg, ast.arg)
    return isinstance(arg, ast.Name)


error = namedtuple('error', 'lineno col message type vars')
Error = partial(partial, error, type=BugBearChecker, vars=())


disabled_by_default = [
    'B901',
    'B902',
    'B903',
    'B950',
]
B001 = Error(
    message="B001 Do not use bare `except:`, it also catches unexpected "
            "events like memory errors, interrupts, system exit, and so on.  "
            "Prefer `except Exception:`.  If you're sure what you're doing, "
            "be explicit and write `except BaseException:`.",
)
B002 = Error(
    message="B002 Python does not support the unary prefix increment. Writing "
            "++n is equivalent to +(+(n)), which equals n. You meant n += 1.",
)
B003 = Error(
    message="B003 Assigning to `os.environ` doesn't clear the environment. "
            "Subprocesses are going to see outdated variables, in disagreement "
            "with the current process. Use `os.environ.clear()` or the `env=` "
            "argument to Popen.",
)
B004 = Error(
    message="B004 Using `hasattr(x, '__call__')` to test if `x` is callable "
            "is unreliable. If `x` implements custom `__getattr__` or its "
            "`__call__` is itself not callable, you might get misleading "
            "results. Use `callable(x)` for consistent results.",
)
B005 = Error(
    message="B005 Using .strip() with multi-character strings is misleading "
            "the reader. It looks like stripping a substring. Move your "
            "character set to a constant if this is deliberate. Use "
            ".replace() or regular expressions to remove string fragments.",
)
B005.methods = {'lstrip', 'rstrip', 'strip'}
B005.valid_paths = {}

B006 = Error(
    message="B006 Do not use mutable data structures for argument defaults. "
            "All calls reuse one instance of that data structure, persisting "
            "changes between them.",
)
B006.mutable_literals = (ast.Dict, ast.List, ast.Set)
B006.mutable_calls = {
    'Counter',
    'OrderedDict',
    'collections.Counter',
    'collections.OrderedDict',
    'collections.defaultdict',
    'collections.deque',
    'defaultdict',
    'deque',
    'dict',
    'list',
    'set',
}
B007 = Error(
    message="B007 Loop control variable {vars} not used within the loop body. "
            "If this is intended, start the name with an underscore.",
)


B301 = Error(
    message="B301 Python 3 does not include `.iter*` methods on dictionaries. "
            "Remove the `iter` prefix from the method name. For Python 2 "
            "compatibility, prefer the Python 3 equivalent unless you expect "
            "the size of the container to be large or unbounded. Then use "
            "`six.iter*` or `future.utils.iter*`.",
)
B301.methods = {'iterkeys', 'itervalues', 'iteritems', 'iterlists'}
B301.valid_paths = {'six', 'future.utils', 'builtins'}

B302 = Error(
    message="B302 Python 3 does not include `.view*` methods on dictionaries. "
            "Remove the `view` prefix from the method name. For Python 2 "
            "compatibility, prefer the Python 3 equivalent unless you expect "
            "the size of the container to be large or unbounded. Then use "
            "`six.view*` or `future.utils.view*`.",
)
B302.methods = {'viewkeys', 'viewvalues', 'viewitems', 'viewlists'}
B302.valid_paths = {'six', 'future.utils', 'builtins'}

B303 = Error(
    message="B303 `__metaclass__` does nothing on Python 3. Use "
            "`class MyClass(BaseClass, metaclass=...)`. For Python 2 "
            "compatibility, use `six.add_metaclass`.",
)
B304 = Error(
    message="B304 `sys.maxint` is not a thing on Python 3. Use `sys.maxsize`.",
)
B305 = Error(
    message="B305 `.next()` is not a thing on Python 3. Use the `next()` "
            "builtin. For Python 2 compatibility, use `six.next()`.",
)
B305.methods = {'next'}
B305.valid_paths = {'six', 'future.utils', 'builtins'}

B306 = Error(
    message="B306 `BaseException.message` has been deprecated as of Python "
            "2.6 and is removed in Python 3. Use `str(e)` to access the "
            "user-readable message. Use `e.args` to access arguments passed "
            "to the exception.",
)
B901 = Error(
    message="B901 Using `yield` together with `return x`. Use native "
            "`async def` coroutines or put a `# noqa` comment on this "
            "line if this was intentional.",
)
B902 = Error(
    message="B902 Invalid first argument {vars} used for {vars} method. Use the "
            "canonical first argument name in methods, i.e. {vars}.",
)
B902.implicit_classmethods = {'__new__', '__init_subclass__'}
B902.self = ['self']  # it's a list because the first is preferred
B902.cls = ['cls', 'klass']  # ditto.
B902.metacls = ['metacls', 'metaclass', 'typ']  # ditto.

B903 = Error(
    message="B903 Data class should either be immutable or use __slots__ to "
            "save memory. Use collections.namedtuple to generate an "
            "immutable class, or enumerate the attributes in a __slot__ "
            "declaration in the class to leave attributes mutable.",
)
B950 = Error(
    message="B950 line too long ({vars} > {vars} characters)",
)
