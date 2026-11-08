import os
import sys

def add_parent_dir_to_sys_path(fileName):
    current_dir = os.path.dirname(os.path.abspath(fileName))
    parent_dir = os.path.dirname(current_dir)
    if parent_dir not in sys.path:
        print(f"Adding {parent_dir} to sys.path")
        sys.path.insert(0, parent_dir)

