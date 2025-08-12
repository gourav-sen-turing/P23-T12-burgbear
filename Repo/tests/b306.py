"""
Should emit:
B306 - on line 9
"""

try:
    import some_library
except ImportError as e:
    print(e.message)
else:
    print(some_library.message)
