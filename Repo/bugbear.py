#!/usr/bin/env python3
import ast
from collections import namedtuple
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
        if self.should_warn('B950'):
            # B950 allows 10% over the limit
            max_length = int(self.max_line_length * 1.1)

            for lineno, line in enumerate(self.lines, 1):
                # Skip lines with noqa
                if '# noqa' in line or '# type:' in line:
                    continue

                length = len(line.rstrip())
                if length > max_length:
                    yield B950(lineno, length,
                               vars=(length, self.max_line_length))

    @classmethod
    def adapt_error(cls, e):
        """Adapts the extended error namedtuple to be compatible with Flake8."""
        return e._replace(message=e.message.format(*e.vars))[:4]

    def load_file(self):
        """Loads the file in a way that auto-detects source encoding and deals
        with broken terminal encodings for stdin.

        Stolen from flake8_import_order because it's good.
        """

        if self.filename in ("stdin", "-", None):
            self.filename = "stdin"
            self.lines = pycodestyle.stdin_get_value().splitlines(True)
            self.tree = ast.parse("".join(self.lines))
        else:
            index = 0
            if self.options is not None:
                index = self.options.hang_closing
            with open(self.filename, 'rb') as f:
                source = f.read()
            try:
                self.tree = ast.parse(source)
            except SyntaxError as e:
                e.lineno = 1
                raise e
            self.lines = source.decode('utf-8').splitlines(True)

    @classmethod
    def add_options(cls, parser):
        parser.add_option(
            '--max-line-length', type=int, metavar='n',
            default=79, help='Maximum allowed line length for the entirety of this'
            ' run. (Default: %default)'
        )

    @classmethod
    def parse_options(cls, options):
        return

    def should_warn(self, code):
        """
        Returns `True` if Bugbear should emit a particular warning.
        flake8 overrides default ignores when the user specifies
        `ignore = ` in configuration.  This is problematic because it means
        specifying anything in `ignore = ` implicitly enables all optional
        warnings.  This function is a workaround for this behavior.
        """
        # For testing, when options is None, we enable all checks
        if self.options is None:
            return True

        # Check if code is disabled by default
        if code in disabled_by_default:
            # Check if it's explicitly selected
            if hasattr(self.options, 'select') and self.options.select:
                if code in self.options.select:
                    return True
            return False

        # For B950
        if code[:3] == 'B95':
            if code[:4] not in getattr(self.options, 'select', []):
                # flake8 >=3.0 supports both `select` and `ignore`. If codes
                # are selected explicitly, assume the user wanted B950.
                return False

            if hasattr(self.options, 'ignore') and self.options.ignore and code[:4] in self.options.ignore:
                # flake8 >=3.0. If the user explicitly ignores warnings, let
                # them do so.
                return False

        # Normal warnings are safe for emission.
        return True


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
        if (isinstance(node.op, ast.UAdd) and
            isinstance(node.operand, ast.UnaryOp) and
            isinstance(node.operand.op, ast.UAdd)):
            self.errors.append(B002(node.lineno, node.col_offset))
        self.generic_visit(node)

    def visit_Call(self, node):
        self.check_for_b004(node)
        self.check_for_b005(node)
        self.check_for_b301_b302_b305(node)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        self.check_for_b304(node)
        self.check_for_b306(node)
        self.generic_visit(node)

    def visit_Assign(self, node):
        self.check_for_b003(node)
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

    def check_for_b301_b302_b305(self, node):
        if not isinstance(node.func, ast.Attribute):
            return

        method = node.func.attr

        # Get the base path to check if it's a valid alternative like six.*
        base_path = '.'.join(self.compose_call_path(node.func.value))

        if method in B301.methods:
            if base_path not in B301.valid_paths:
                self.errors.append(B301(node.lineno, node.col_offset))
        elif method in B302.methods:
            if base_path not in B302.valid_paths:
                self.errors.append(B302(node.lineno, node.col_offset))
        elif method in B305.methods:
            if base_path not in B305.valid_paths:
                self.errors.append(B305(node.lineno, node.col_offset))

    def check_for_b304(self, node):
        if (node.attr == 'maxint' and
            isinstance(node.value, ast.Name) and
            node.value.id == 'sys'):
            self.errors.append(B304(node.lineno, node.col_offset))

    def check_for_b306(self, node):
        if (node.attr == 'message' and
            isinstance(node.value, ast.Name) and
            isinstance(node.ctx, ast.Load)):
            # Check if we're in an except handler and the name matches
            for i, ancestor in enumerate(reversed(self.node_stack)):
                if isinstance(ancestor, ast.ExceptHandler):
                    if ancestor.name:
                        # Python 3 style
                        exc_name = ancestor.name if isinstance(ancestor.name, str) else ancestor.name.id
                    else:
                        # No explicit name binding
                        break
                    if node.value.id == exc_name:
                        self.errors.append(B306(node.lineno, node.col_offset))
                    break

    def check_for_b003(self, node):
        if len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Attribute):
                if (target.attr == 'environ' and
                    isinstance(target.value, ast.Name) and
                    target.value.id == 'os'):
                    self.errors.append(B003(node.lineno, node.col_offset))
            elif isinstance(target, ast.Name):
                if target.id == '__metaclass__':
                    self.errors.append(B303(node.lineno, node.col_offset))

    def check_for_b004(self, node):
        if (isinstance(node.func, ast.Name) and
            node.func.id in ('getattr', 'hasattr') and
            len(node.args) >= 2):
            arg = node.args[1]
            value = None
            if isinstance(arg, ast.Str):
                value = arg.s
            elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                value = arg.value
            if value == '__call__':
                self.errors.append(B004(node.lineno, node.col_offset))

    def check_for_b005(self, node):
        if (isinstance(node.func, ast.Attribute) and
            node.func.attr in ('strip', 'lstrip', 'rstrip') and
            len(node.args) == 1 and
            isinstance(node.args[0], (ast.Str, ast.Constant))):
            # Get the string value
            if isinstance(node.args[0], ast.Str):
                value = node.args[0].s
            else:
                value = node.args[0].value
            if isinstance(value, str) and len(value) > 1:
                # Check if it's a raw string literal (contains literal backslashes)
                if '\\' in value:
                    self.errors.append(B005(node.lineno, node.col_offset))
                    return
                # Don't warn for common whitespace combinations
                whitespace_chars = set(' \t\n\r\f\v')
                if set(value).issubset(whitespace_chars):
                    return
                # Don't warn for simple 2-char strips that look intentional
                if len(value) == 2:
                    return
                # Check for patterns that look like substrings
                # (contain dots, slashes, or multiple of the same char)
                if ('.' in value or '/' in value or
                    any(value.count(c) > 1 for c in set(value))):
                    self.errors.append(B005(node.lineno, node.col_offset))

    def check_for_b006(self, node):
        for arg in node.args.defaults + node.args.kw_defaults:
            if arg is None:
                continue
            if isinstance(arg, (ast.List, ast.Dict, ast.Set, ast.Call)):
                if isinstance(arg, ast.Call):
                    path = '.'.join(self.compose_call_path(arg.func))
                    if path in B006.mutable_calls:
                        self.errors.append(B006(arg.lineno, arg.col_offset))
                else:
                    self.errors.append(B006(arg.lineno, arg.col_offset))

    def check_for_b007(self, node):
        # Check if we're in a nested loop - if so, be more conservative
        in_nested_loop = any(isinstance(n, ast.For) for n in self.node_stack[:-1])

        # Helper to check if a name is used
        def name_is_used(name, body):
            finder = NameFinder()
            for stmt in body:
                finder.visit(stmt)
            return name in finder.names

        if isinstance(node.target, ast.Name):
            target_name = node.target.id
            if not target_name.startswith('_') and not name_is_used(target_name, node.body):
                # Don't report if we're in a complex nested structure
                if not in_nested_loop or not any(isinstance(stmt, ast.For) for stmt in node.body):
                    self.errors.append(
                        B007(node.lineno, node.target.col_offset, vars=(target_name,))
                    )
        elif isinstance(node.target, (ast.Tuple, ast.List)):
            # Handle tuple unpacking
            self._check_tuple_names(node.target, node.body, node.lineno)

    def _check_tuple_names(self, target, body, lineno):
        """Recursively check names in tuple unpacking."""
        if isinstance(target, ast.Name):
            if not target.id.startswith('_'):
                finder = NameFinder()
                for stmt in body:
                    finder.visit(stmt)
                if target.id not in finder.names:
                    self.errors.append(
                        B007(lineno, target.col_offset, vars=(target.id,))
                    )
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._check_tuple_names(elt, body, lineno)

    def check_for_b901(self, node):
        # Check if function has both yield and return with value
        # But don't check nested functions
        has_yield = False
        return_nodes = []

        def check_node(n, in_nested_func=False):
            nonlocal has_yield
            for child in ast.iter_child_nodes(n):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Don't recurse into nested functions
                    continue
                elif isinstance(child, (ast.Yield, ast.YieldFrom)):
                    has_yield = True
                elif isinstance(child, ast.Return) and child.value is not None:
                    return_nodes.append(child)
                else:
                    check_node(child)

        check_node(node)

        if has_yield and return_nodes:
            for ret in return_nodes:
                self.errors.append(B901(ret.lineno, ret.col_offset))

    def has_yield(self, node):
        for child in ast.walk(node):
            if isinstance(child, (ast.Yield, ast.YieldFrom)):
                return True
        return False

    def has_return_with_value(self, node):
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and child.value is not None:
                return True
        return False

    def check_for_b902(self, node):
        if not self.node_stack:
            return

        parent = self.node_stack[-2] if len(self.node_stack) >= 2 else None
        if not parent or not isinstance(parent, ast.ClassDef):
            return

        # Get expected first argument name
        expected = None
        decorators = {d.id for d in node.decorator_list if isinstance(d, ast.Name)}

        if 'staticmethod' in decorators:
            return  # No check for staticmethods
        elif 'classmethod' in decorators:
            expected = B902.cls
        elif node.name in B902.implicit_classmethods:
            expected = B902.cls
        else:
            # Check if parent is a metaclass
            # Simple heuristic: inherits from type
            is_metaclass = False
            if parent.bases:
                for base in parent.bases:
                    if isinstance(base, ast.Name) and base.id == 'type':
                        is_metaclass = True
                        break

            if is_metaclass:
                # In metaclasses, only __prepare__ uses metacls
                if node.name == '__prepare__':
                    expected = B902.metacls
                else:
                    expected = B902.cls
            else:
                expected = B902.self

        # Check various types of first arguments
        first_arg = None
        actual = None
        lineno = node.lineno
        col_offset = node.col_offset

        if node.args.args:
            first_arg = node.args.args[0]
            if hasattr(first_arg, 'arg'):
                actual = first_arg.arg
            else:
                actual = first_arg.id
            lineno = first_arg.lineno
            col_offset = first_arg.col_offset
        elif node.args.vararg:
            # *args is the only positional argument
            actual = '*args'
            lineno = node.args.vararg.lineno if hasattr(node.args.vararg, 'lineno') else node.lineno
            col_offset = node.args.vararg.col_offset if hasattr(node.args.vararg, 'col_offset') else node.col_offset
        elif node.args.kwonlyargs:
            # Keyword only arguments like (*, self)
            actual = '*, ' + ', '.join(arg.arg if hasattr(arg, 'arg') else arg.id for arg in node.args.kwonlyargs)
            if node.args.kwonlyargs:
                lineno = node.args.kwonlyargs[0].lineno if hasattr(node.args.kwonlyargs[0], 'lineno') else node.lineno
                col_offset = node.args.kwonlyargs[0].col_offset if hasattr(node.args.kwonlyargs[0], 'col_offset') else node.col_offset
        elif node.args.kwarg:
            # **kwargs is the only argument
            actual = '**kwargs'
            lineno = node.args.kwarg.lineno if hasattr(node.args.kwarg, 'lineno') else node.lineno
            col_offset = node.args.kwarg.col_offset if hasattr(node.args.kwarg, 'col_offset') else node.col_offset
        else:
            # No arguments at all
            actual = '(none)'

        if expected and (actual == '(none)' or actual not in expected):
            if actual in ['*args', '**kwargs', '(none)'] or actual.startswith('*,'):
                actual_repr = actual
            else:
                actual_repr = repr(actual)

            arg_type = self.get_arg_name_for_b902(node, expected)
            self.errors.append(
                B902(lineno, col_offset,
                     vars=(actual_repr, arg_type, expected[0]))
            )

    def get_arg_name_for_b902(self, node, expected):
        if expected == B902.cls:
            if 'classmethod' in {d.id for d in node.decorator_list if isinstance(d, ast.Name)}:
                return 'class'
            else:
                # Check if we're in a metaclass
                parent = self.node_stack[-2] if len(self.node_stack) >= 2 else None
                if parent and parent.bases:
                    for base in parent.bases:
                        if isinstance(base, ast.Name) and base.id == 'type':
                            # This is a metaclass instance method
                            return 'metaclass instance'
                return 'instance'
        elif expected == B902.metacls:
            # Only __prepare__ in metaclasses
            return 'metaclass class'
        else:
            return 'instance'

    def check_for_b903(self, node):
        """Check if a class is a simple data class that could use __slots__ or namedtuple."""
        # Check if class already has __slots__
        has_slots = any(
            isinstance(stmt, ast.Assign) and
            any(isinstance(target, ast.Name) and target.id == '__slots__'
                for target in stmt.targets)
            for stmt in node.body
            if isinstance(stmt, ast.Assign)
        )

        if has_slots:
            return

        # Filter out docstrings and get actual statements
        body_statements = []
        for i, stmt in enumerate(node.body):
            # Skip docstrings (string constants at the beginning)
            if (i == 0 and isinstance(stmt, ast.Expr) and
                isinstance(stmt.value, (ast.Str, ast.Constant))):
                continue
            body_statements.append(stmt)

        # Check if there are any class-level assignments (class attributes)
        has_class_attributes = any(
            isinstance(stmt, ast.Assign)
            for stmt in body_statements
        )

        if has_class_attributes:
            return

        # Check if class has exactly one method and it's __init__
        methods = [stmt for stmt in body_statements if isinstance(stmt, ast.FunctionDef)]

        if len(methods) != 1 or methods[0].name != '__init__':
            return

        init_method = methods[0]

        # Check if __init__ only contains simple assignments
        # Skip docstrings in __init__ as well
        init_body = []
        for i, stmt in enumerate(init_method.body):
            if (i == 0 and isinstance(stmt, ast.Expr) and
                isinstance(stmt.value, (ast.Str, ast.Constant))):
                continue
            init_body.append(stmt)

        # All remaining statements should be simple assignments of the form self.x = x
        for stmt in init_body:
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
    message="B002 Python does not support the unary prefix increment. "
            "Writing ++n is equivalent to +(+(n)), which equals n. "
            "You meant n += 1.",
)
B003 = Error(
    message="B003 Assigning to `os.environ` doesn't clear the "
            "environment. Subprocesses are going to see outdated "
            "variables, in disagreement with the current process. "
            "Use `os.environ.clear()` or the `env=` argument to "
            "Popen.",
)
B004 = Error(
    message="B004 Using `hasattr(x, '__call__')` to test if `x` is callable "
            "is unreliable. Use `callable(x)` for consistent results.",
)
B005 = Error(
    message="B005 Using .strip() with multi-character strings is misleading "
            "the reader. It looks like stripping a substring. Move your "
            "character set to a constant if this is deliberate. Use "
            ".replace() or regular expressions to remove string fragments.",
)
B006 = Error(
    message="B006 Do not use mutable data structures for argument defaults. "
            "All calls reuse one instance of the default, creating unexpected "
            "behavior as mutations persist between calls.",
)
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
    message="B007 Loop control variable {0} not used within the loop body. "
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
    message="B902 Invalid first argument {0} used for {1} method. Use the "
            "canonical first argument name in methods, i.e. {2}.",
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
    message="B950 line too long ({0} > {1} characters)",
)
