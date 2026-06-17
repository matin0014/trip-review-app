"""Pure trip-cleaning engine.

No Flask, no global state, no I/O side effects except the explicit `loader`.
Dependency direction is one-way:

    params / models / geo / timeparse
        -> normalize / routes
        -> loader
        -> velocity
        -> classify
        -> ids / proposals

Nothing in `engine` imports from `app`.
"""
