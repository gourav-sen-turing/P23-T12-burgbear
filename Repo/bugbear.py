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
        for lineno, line in enumerate(self.lines, 1):
            # Skip lines with noqa
            if '# noqa' in line:
                continue

            # B950: Line too long (with 10% tolerance)
            length = len(line.rstrip())
            limit = self.max_line_length
            # Allow 10% tolerance
            if length > limit * 1.1:
                yield B950(lineno, length, vars=(length, limit))

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
        if node.type is None:
            self.errors.append(B001(node.lineno, node.col_offset))
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.UAdd) and isinstance(node.operand, ast.UnaryOp) and isinstance(node.operand.op, ast.UAdd):
            self.errors.append(B002(node.lineno, node.col_offset))
        self.generic_visit(node)

    def visit_Call(self, node):
        self.check_for_b004(node)
        self.check_for_b005(node)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # Get the call path to check for valid prefixes
        call_path = list(self.compose_call_path(node))

        # Check for B301, B302, B305
        if node.attr in B301.methods:
            # Check if it has a valid prefix
            if len(call_path) >= 2:
                prefix = '.'.join(call_path[:-1])
                if prefix not in B301.valid_paths:
                    self.errors.append(B301(node.lineno, node.col_offset))
            else:
                self.errors.append(B301(node.lineno, node.col_offset))
        elif node.attr in B302.methods:
            # Check if it has a valid prefix
            if len(call_path) >= 2:
                prefix = '.'.join(call_path[:-1])
                if prefix not in B302.valid_paths:
                    self.errors.append(B302(node.lineno, node.col_offset))
            else:
                self.errors.append(B302(node.lineno, node.col_offset))
        elif node.attr in B305.methods:
            # Check if it has a valid prefix
            if len(call_path) >= 2:
                prefix = '.'.join(call_path[:-1])
                if prefix not in B305.valid_paths:
                    self.errors.append(B305(node.lineno, node.col_offset))
            else:
                self.errors.append(B305(node.lineno, node.col_offset))
        elif node.attr == 'message' and isinstance(node.value, ast.Name):
            # Check for B306 - BaseException.message
            # Only flag if it looks like an exception variable (short name, common patterns)
            name = node.value.id
            if len(name) <= 3 or name in {'exc', 'exception', 'error', 'err'}:
                self.errors.append(B306(node.lineno, node.col_offset))
        elif node.attr == 'maxint' and isinstance(node.value, ast.Name) and node.value.id == 'sys':
            # Check for B304 - sys.maxint
            self.errors.append(B304(node.lineno, node.col_offset))
        self.generic_visit(node)

    def visit_Assign(self, node):
        for target in node.targets:
            if (isinstance(target, ast.Attribute) and
                isinstance(target.value, ast.Name) and
                target.value.id == 'os' and
                target.attr == 'environ'):
                self.errors.append(B003(node.lineno, node.col_offset))
            elif isinstance(target, ast.Name) and target.id == '__metaclass__':
                # Check if we're in a class
                for n in self.node_stack:
                    if isinstance(n, ast.ClassDef):
                        self.errors.append(B303(node.lineno, node.col_offset))
                        break
        self.generic_visit(node)

    def visit_For(self, node):
        self.check_for_b007(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.check_for_b006(node)
        self.check_for_b901(node)
        self.check_for_b902(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.check_for_b903(node)
        self.generic_visit(node)

    def compose_call_path(self, node):
        if isinstance(node, ast.Attribute):
            yield from self.compose_call_path(node.value)
            yield node.attr
        elif isinstance(node, ast.Name):
            yield node.id

    def check_for_b004(self, node):
        if (isinstance(node.func, ast.Name) and
            (node.func.id == 'hasattr' or node.func.id == 'getattr')):
            if len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Str):
                    if arg.s == '__call__':
                        self.errors.append(B004(node.lineno, node.col_offset))
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value == '__call__':
                        self.errors.append(B004(node.lineno, node.col_offset))

    def check_for_b005(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr in {'strip', 'lstrip', 'rstrip'}:
            if len(node.args) == 1:
                arg = node.args[0]
                if isinstance(arg, ast.Str):
                    value = arg.s
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    value = arg.value
                else:
                    return

                # Check if it's a multi-character string that looks like a substring
                # rather than a character set
                if len(value) > 1 and not (set(value) <= {' ', '\t', '\n', '\r', '\f', '\v'}):
                    # Warn if it contains special characters that make it look like a path/URL/escape
                    if any(c in value for c in '.:/\\'):
                        self.errors.append(B005(node.lineno, node.col_offset))

    def check_for_b006(self, node):
        for default in node.args.defaults + node.args.kw_defaults:
            if default is None:
                continue
            if self._is_mutable_default(default):
                self.errors.append(B006(default.lineno, default.col_offset))

    def _is_mutable_default(self, node):
        if isinstance(node, ast.List):
            return True
        if isinstance(node, ast.Dict):
            return True
        if isinstance(node, ast.Set):
            return True
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in B006.mutable_calls:
                    return True
            elif isinstance(node.func, ast.Attribute):
                call_path = '.'.join(self.compose_call_path(node.func))
                if call_path in B006.mutable_calls:
                    return True
        return False

    def check_for_b007(self, node):
        # Find all names defined in the loop target
        names = self._get_names_from_target(node.target)

        # Check if they are used in the loop body
        for name in names:
            if name.id == '_':  # underscore is okay
                continue
            name_finder = NameFinder()
            # Only check the body of this loop, not nested loops
            for stmt in node.body:
                name_finder.visit(stmt)

            if name.id not in name_finder.names:
                self.errors.append(B007(name.lineno, name.col_offset, vars=(name.id,)))

    def _get_names_from_target(self, target):
        if isinstance(target, ast.Name):
            return [target]
        elif isinstance(target, (ast.Tuple, ast.List)):
            names = []
            for elt in target.elts:
                names.extend(self._get_names_from_target(elt))
            return names
        else:
            return []

    def check_for_b901(self, node):
        # Check if function has both yield and return with value
        has_yield = False
        return_nodes = []

        # Use a custom visitor to avoid nested functions
        class FunctionBodyVisitor(ast.NodeVisitor):
            def __init__(self):
                self.has_yield = False
                self.return_nodes = []

            def visit_FunctionDef(self, node):
                # Don't visit nested functions
                pass

            def visit_AsyncFunctionDef(self, node):
                # Don't visit nested functions
                pass

            def visit_Yield(self, node):
                self.has_yield = True
                self.generic_visit(node)

            def visit_YieldFrom(self, node):
                self.has_yield = True
                self.generic_visit(node)

            def visit_Return(self, node):
                if node.value is not None:
                    self.return_nodes.append(node)
                self.generic_visit(node)

        visitor = FunctionBodyVisitor()
        for stmt in node.body:
            visitor.visit(stmt)

        if visitor.has_yield and visitor.return_nodes:
            for return_node in visitor.return_nodes:
                self.errors.append(B901(return_node.lineno, return_node.col_offset))

    def check_for_b902(self, node):
        if not self.node_stack:
            return

        # Check if this function is inside a class
        parent = None
        for i in range(len(self.node_stack) - 1, -1, -1):
            if isinstance(self.node_stack[i], ast.ClassDef):
                parent = self.node_stack[i]
                break
            elif isinstance(self.node_stack[i], (ast.FunctionDef, ast.AsyncFunctionDef)) and self.node_stack[i] != node:
                # If we hit another function before finding a class, this is a nested function
                return

        if not parent:
            return

        # Skip static methods
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == 'staticmethod':
                return

        # Get expected first argument name
        is_classmethod = any(
            isinstance(decorator, ast.Name) and decorator.id == 'classmethod'
            for decorator in node.decorator_list
        )

        # Check if parent is a metaclass
        is_metaclass = False
        if parent.bases:
            for base in parent.bases:
                if isinstance(base, ast.Name) and base.id == 'type':
                    is_metaclass = True
                    break

        if is_classmethod or node.name in B902.implicit_classmethods:
            if is_metaclass:
                expected = B902.metacls
                method_type = 'metaclass class'
            else:
                expected = B902.cls
                method_type = 'class'
        else:
            if is_metaclass:
                expected = B902.cls
                method_type = 'metaclass instance'
            else:
                expected = B902.self
                method_type = 'instance'

        # Check first argument

        if not node.args.args and not node.args.posonlyargs:
            # No positional arguments at all
            if node.args.vararg:
                # Only *args (and maybe **kwargs)
                self.errors.append(B902(node.args.vararg.lineno, node.args.vararg.col_offset,
                                      vars=('*' + node.args.vararg.arg, method_type, expected[0])))
            elif node.args.kwonlyargs:
                # Only keyword-only args
                first_kwonly = node.args.kwonlyargs[0]
                self.errors.append(B902(first_kwonly.lineno, first_kwonly.col_offset,
                                      vars=('*, ' + first_kwonly.arg, method_type, expected[0])))
            elif node.args.kwarg:
                # Only **kwargs
                self.errors.append(B902(node.args.kwarg.lineno, node.args.kwarg.col_offset,
                                      vars=('**' + node.args.kwarg.arg, method_type, expected[0])))
            else:
                # No arguments at all
                self.errors.append(B902(node.lineno, node.col_offset,
                                      vars=('(none)', method_type, expected[0])))
        else:
            # Get first argument
            if node.args.posonlyargs:
                first_arg = node.args.posonlyargs[0]
            elif node.args.args:
                first_arg = node.args.args[0]
            else:
                return

            if first_arg.arg not in expected:
                self.errors.append(B902(first_arg.lineno, first_arg.col_offset,
                                      vars=(repr(first_arg.arg), method_type, expected[0])))

    def check_for_b903(self, node):
        """Check for simple data classes that could use namedtuple or __slots__."""
        # Check if class already has __slots__
        has_slots = any(
            isinstance(item, ast.Assign) and
            any(isinstance(target, ast.Name) and target.id == '__slots__'
                for target in item.targets)
            for item in node.body
            if isinstance(item, ast.Assign)
        )

        if has_slots:
            return

        # Find all methods and class attributes
        methods = []
        class_attrs = []

        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                methods.append(item)
            elif isinstance(item, ast.Assign):
                class_attrs.append(item)
            elif isinstance(item, ast.AnnAssign):
                class_attrs.append(item)
            # Ignore docstrings (Expr nodes with Str/Constant)
            elif isinstance(item, ast.Expr):
                if isinstance(item.value, (ast.Str, ast.Constant)):
                    continue
                else:
                    # Other expressions are considered class attributes
                    class_attrs.append(item)

        # Check if there are any class attributes
        if class_attrs:
            return

        # Check if there's exactly one method and it's __init__
        if len(methods) != 1 or methods[0].name != '__init__':
            return

        init_method = methods[0]

        # Check if __init__ only contains simple assignments
        # Skip the docstring if present
        body = init_method.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, (ast.Str, ast.Constant)):
            if isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            elif isinstance(body[0].value, ast.Str):
                body = body[1:]

        # Must have at least one assignment
        if not body:
            return

        # All remaining statements should be simple assignments of the form self.x = x
        for stmt in body:
            if not isinstance(stmt, ast.Assign):
                return

            # Should have exactly one target
            if len(stmt.targets) != 1:
                return

            target = stmt.targets[0]

            # Target should be an attribute of self
            if not (isinstance(target, ast.Attribute) and
                    isinstance(target.value, ast.Name) and
                    target.value.id == 'self'):
                return

            # Value should be a simple name (parameter)
            if not isinstance(stmt.value, ast.Name):
                return

        # If we get here, it's a simple data class
        self.errors.append(B903(node.lineno, node.col_offset))


@attr.s
class NameFinder(ast.NodeVisitor):
    """Finds a name within a tree of nodes."""
    names = attr.ib(default=attr.Factory(dict))

    def visit_Name(self, node):
        self.names.setdefault(node.id, []).append(node)
        self.generic_visit(node)


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
