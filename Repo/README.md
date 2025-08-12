# Turn 1: Model A response

## Summary of B903 Implementation Requirements:

### What B903 Should Detect:
1. Classes that only contain an `__init__` method
2. The `__init__` method only performs simple parameter-to-attribute assignments (e.g., `self.foo = foo`)
3. The class has no other methods besides `__init__`
4. The class has no class-level attributes (except possibly a docstring)

### What B903 Should NOT Detect:
1. Classes with additional methods beyond `__init__`
2. Classes with class-level attributes
3. Classes where `__init__` does complex operations beyond simple assignment
4. Classes where there are other statements in `__init__` besides direct assignments

### Implementation Approach:

1. **Hook into `visit_ClassDef`**: This method will be called for each class definition in the AST.

2. **Check class body**:
- Count the number of function definitions (should be exactly 1, and it should be `__init__`)
- Check for class-level attributes (should be none, except docstrings)
- Verify the `__init__` method only contains simple assignments

3. **Validate `__init__` method**:
- All statements should be assignments of the form `self.attr = param`
- The right-hand side should be a simple Name node matching a parameter
- No complex expressions or additional logic

4. **Error reporting**: If all conditions are met, add a B903 error at the class definition line.

### Key Files to Modify:
1. **bugbear.py**: Implement the `visit_ClassDef` method and `check_for_b903` helper
2. **README.rst**: Already has documentation for B903
3. **disabled_by_default**: Already includes 'B903'

The implementation needs to follow the existing patterns in the codebase, using the visitor pattern and the error reporting mechanism already established.
