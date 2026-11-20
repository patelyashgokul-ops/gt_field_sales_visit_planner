from . import models
from . import wizard


def pre_init_hook(env):
    # GiST equality operators for integer foreign keys in the assignment
    # exclusion constraint. This is a trusted PostgreSQL extension.
    env.cr.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')
from . import controllers

