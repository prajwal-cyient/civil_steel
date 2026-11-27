import ast
import contextlib
import importlib
import io
import itertools
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# agents.A4_staad.staad_com_helpers guards its openstaadpy import, so it imports safely here.
import agents.A4_staad.staad_com_helpers as staad_common  # noqa: E402

# agents.A6_tekla.tekla_open_api loads pythonnet and the Tekla .NET assemblies at import
# time, so extract clean_tekla() on its own (as the STAAD tests do) and run it
# against a namespace that supplies a mocked _get_connected_model().
TEKLA_COMMON_PATH = SRC_DIR / "agents" / "A6_tekla" / "tekla_open_api.py"
_tekla_tree = ast.parse(TEKLA_COMMON_PATH.read_text(encoding="utf-8"))
_clean_tekla_def = next(
    node for node in _tekla_tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "clean_tekla"
)
tekla_namespace = {}
exec("import getpass", tekla_namespace)
exec(
    compile(ast.Module(body=[_clean_tekla_def], type_ignores=[]), str(TEKLA_COMMON_PATH), "exec"),
    tekla_namespace,
)


def clean_tekla():
    return tekla_namespace["clean_tekla"]()


class Enumerator:
    def __init__(self, objects):
        self._objects = iter(objects)

    def MoveNext(self):
        self.Current = next(self._objects, None)
        return self.Current is not None


class ModelObject:
    def __init__(self, identifier):
        self.Identifier = SimpleNamespace(ID=identifier)

    def GetType(self):
        return SimpleNamespace(FullName=type(self).__name__)


class GridBase(ModelObject):
    pass


class GridSurface(ModelObject):
    pass


class BaseComponent(ModelObject):
    pass


class LoadGroup(ModelObject):
    def __init__(self, identifier, name):
        super().__init__(identifier)
        self.GroupName = name


class TeklaCleanupTests(unittest.TestCase):
    def setUp(self):
        self.grid = GridBase(1)
        self.plane = GridSurface(2)
        self.part = ModelObject(3)
        self.component = BaseComponent(4)
        self.objects = [self.grid, self.plane, self.part, self.component]
        self.order = []
        for obj in self.objects:
            obj.Delete = Mock(side_effect=lambda obj=obj: self.delete(obj))
        self.model = Mock()
        self.model.GetModelObjectSelector.return_value.GetAllObjects.side_effect = (
            lambda: Enumerator(list(self.objects))
        )
        self.model.SelectModelObject.side_effect = lambda identifier: next(
            (obj for obj in self.objects if obj.Identifier.ID == identifier.ID), None
        )
        self.model.CommitChanges.return_value = True
        self.handler = Mock()
        self.handler.Save.return_value = True
        connected_model = patch.dict(
            tekla_namespace, {"_get_connected_model": Mock(return_value=self.model)}
        )
        connected_model.start()
        self.addCleanup(connected_model.stop)
        api = ModuleType("Tekla.Structures.Model")
        api.BaseComponent = BaseComponent
        api.GridBase = GridBase
        api.GridSurface = GridSurface
        api.LoadGroup = LoadGroup
        api.ModelHandler = Mock(return_value=self.handler)
        self.modules = patch.dict(sys.modules, {
            "Tekla": ModuleType("Tekla"),
            "Tekla.Structures": ModuleType("Tekla.Structures"),
            "Tekla.Structures.Model": api,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def delete(self, obj):
        self.order.append(obj.Identifier.ID)
        self.objects.remove(obj)
        return True

    def test_components_first_and_grids_preserved_and_saved(self):
        clean_tekla()
        self.assertEqual(self.order, [4, 3])
        self.assertEqual(self.objects, [self.grid, self.plane])
        self.grid.Delete.assert_not_called()
        self.plane.Delete.assert_not_called()
        self.model.CommitChanges.assert_called_once()
        self.handler.Save.assert_called_once()

    def test_cascaded_deletions_are_not_repeated(self):
        def delete_component():
            self.delete(self.component)
            self.delete(self.part)
            return True
        self.component.Delete.side_effect = delete_component
        clean_tekla()
        self.part.Delete.assert_not_called()

    def test_failed_delete_raises_without_saving(self):
        self.part.Delete.side_effect = None
        self.part.Delete.return_value = False
        with self.assertRaisesRegex(RuntimeError, "Cannot delete object IDs: 3"):
            clean_tekla()
        self.handler.Save.assert_not_called()

    def test_success_return_without_deletion_does_not_loop_forever(self):
        self.part.Delete.side_effect = None
        self.part.Delete.return_value = True
        with self.assertRaisesRegex(RuntimeError, "cleanup incomplete"):
            clean_tekla()

    def test_dependent_objects_are_retried(self):
        self.assembly = ModelObject(5)
        self.objects.insert(2, self.assembly)
        self.assembly.Delete = Mock(side_effect=lambda: (
            False if self.part in self.objects else self.delete(self.assembly)
        ))
        clean_tekla()
        self.assertEqual(self.assembly.Delete.call_count, 2)

    def test_unexpected_grid_deletion_is_reported(self):
        def delete_component():
            self.delete(self.component)
            self.delete(self.grid)
            return True
        self.component.Delete.side_effect = delete_component
        with self.assertRaisesRegex(RuntimeError, "grids changed"):
            clean_tekla()
        self.handler.Save.assert_not_called()

    def test_commit_failure_is_reported(self):
        self.model.CommitChanges.return_value = False
        with self.assertRaisesRegex(RuntimeError, "could not be committed"):
            clean_tekla()
        self.handler.Save.assert_not_called()

    def test_save_failure_is_reported(self):
        self.handler.Save.return_value = False
        with self.assertRaisesRegex(RuntimeError, "saving the model failed"):
            clean_tekla()

    def test_grid_only_model_is_saved(self):
        self.objects = [self.grid, self.plane]
        clean_tekla()
        self.handler.Save.assert_called_once()

    def test_default_load_group_is_preserved(self):
        group = LoadGroup(23997309, "DefaultGroup")
        group.Delete = Mock(return_value=False)
        self.objects.append(group)
        clean_tekla()
        group.Delete.assert_not_called()
        self.assertEqual(self.objects, [self.grid, self.plane, group])
        self.handler.Save.assert_called_once()

    def test_other_load_groups_are_deleted(self):
        group = LoadGroup(6, "Wind")
        group.Delete = Mock(side_effect=lambda: self.delete(group))
        self.objects.append(group)
        clean_tekla()
        group.Delete.assert_called_once()
        self.assertNotIn(group, self.objects)


class StaadCleanupTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(staad_common.time, "monotonic", side_effect=itertools.count(0, 5))
        clock.start()
        self.addCleanup(clock.stop)
        sleep = patch.object(staad_common.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "project.std"
        self.original = "STAAD SPACE\nJOINT COORDINATES\n1 0 0 0\nFINISH\n"
        self.empty_model = staad_common.EMPTY_MODEL_PATH.read_bytes()
        self.path.write_text(self.original, encoding="ascii")
        self.staad = Mock()
        self.filename = str(self.path)
        self.staad.GetSTAADFile.side_effect = lambda: self.filename
        self.staad.IsPhysicalModel.return_value = False
        self.staad.IsAnalyzing.return_value = False
        self.staad.OpenSTAADFile.side_effect = self.open
        for name in ("GetNodeCount", "GetMemberCount", "GetPlateCount", "GetSolidCount"):
            getattr(self.staad.Geometry, name).return_value = 0
        api = ModuleType("openstaadpy")
        api.os_analytical = SimpleNamespace(connect=Mock(return_value=self.staad))
        modules = patch.dict(sys.modules, {"openstaadpy": api})
        modules.start()
        self.addCleanup(modules.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def open(self, filename):
        self.filename = filename

    def backups(self):
        return list(self.directory.glob("*.std.bak"))

    def test_reset_backup_and_reopen_same_path(self):
        # Include unsaved edits in the backup by saving before copying.
        original_save = self.original + "* latest edit\n"
        def save(_silent):
            if not self.backups():
                self.path.write_text(original_save, encoding="ascii")
        self.staad.SaveModel.side_effect = save
        staad_common.clean_staad()
        self.assertEqual(self.path.read_bytes(), self.empty_model)
        self.assertEqual(self.backups()[0].read_text(), original_save)
        self.assertEqual(self.filename, str(self.path))
        self.assertEqual(self.staad.SaveModel.call_count, 2)
        self.assertFalse(list(self.directory.glob("*.cleanup-*.std")))
        self.staad.CloseSTAADFile.assert_not_called()
        opened_paths = [Path(call.args[0]) for call in self.staad.OpenSTAADFile.call_args_list]
        self.assertNotEqual(opened_paths[0], self.path)
        self.assertEqual(opened_paths[1], self.path)

    def test_repeated_cleanup_keeps_previous_backup(self):
        staad_common.clean_staad()
        staad_common.clean_staad()
        self.assertEqual(len(self.backups()), 2)
        self.assertIn(self.original, [path.read_text() for path in self.backups()])

    def test_no_open_file_rejected(self):
        self.filename = ""
        with self.assertRaisesRegex(RuntimeError, "existing saved analytical .std path"):
            staad_common.clean_staad()
        self.staad.SaveModel.assert_not_called()

    def test_missing_file_rejected(self):
        self.path.unlink()
        with self.assertRaisesRegex(RuntimeError, "existing saved analytical .std path"):
            staad_common.clean_staad()
        self.staad.SaveModel.assert_not_called()

    def test_physical_model_rejected(self):
        self.staad.IsPhysicalModel.return_value = True
        with self.assertRaisesRegex(RuntimeError, "Physical models"):
            staad_common.clean_staad()
        self.staad.SaveModel.assert_not_called()

    def test_running_analysis_rejected(self):
        self.staad.IsAnalyzing.return_value = True
        with self.assertRaisesRegex(RuntimeError, "analysis to finish"):
            staad_common.clean_staad()
        self.staad.SaveModel.assert_not_called()

    def test_failed_switch_does_not_overwrite(self):
        # A stale current filename must prevent replacement of the original.
        self.staad.OpenSTAADFile.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "did not open the requested empty model"):
            staad_common.clean_staad()
        self.assertEqual(self.path.read_text(), self.original)

    def test_failed_reopen_reports_backup(self):
        def open_staging_only(filename):
            if Path(filename) != self.path:
                self.open(filename)
        self.staad.OpenSTAADFile.side_effect = open_staging_only
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(RuntimeError, "did not open the requested empty model"):
                staad_common.clean_staad()
        self.assertIn(str(self.backups()[0]), output.getvalue())
        self.assertIn("staging input retained", output.getvalue())
        self.assertTrue(Path(self.filename).exists())
        self.assertEqual(self.backups()[0].read_text(), self.original)

    def test_residual_geometry_rejected(self):
        self.staad.Geometry.GetPlateCount.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "geometry counts: \\(0, 0, 1, 0\\)"):
            staad_common.clean_staad()
        self.assertEqual(self.staad.SaveModel.call_count, 1)

    def test_saved_nonempty_input_rejected(self):
        def save(_silent):
            self.path.write_text(self.original, encoding="ascii")
        self.staad.SaveModel.side_effect = save
        with self.assertRaisesRegex(RuntimeError, "differs from"):
            staad_common.clean_staad()

    def test_missing_template_rejected_before_save(self):
        with patch.object(staad_common, "EMPTY_MODEL_PATH", self.directory / "missing.std"):
            with self.assertRaises(FileNotFoundError):
                staad_common.clean_staad()
        self.staad.SaveModel.assert_not_called()

    def test_final_model_geometry_is_verified(self):
        self.staad.Geometry.GetNodeCount.side_effect = lambda: int(self.filename == str(self.path))
        with self.assertRaisesRegex(RuntimeError, "geometry counts: \\(1, 0, 0, 0\\)"):
            staad_common.clean_staad()
        self.assertEqual(self.staad.SaveModel.call_count, 1)

    def test_asynchronous_open_waits_for_path_and_empty_geometry(self):
        pending = []
        def delayed_open(filename):
            pending[:] = [filename]
        def complete_open(_seconds):
            self.filename = pending[0]
        self.staad.OpenSTAADFile.side_effect = delayed_open
        staad_common.time.sleep.side_effect = complete_open
        # A matching filename can be visible before geometry is refreshed.
        self.staad.Geometry.GetNodeCount.side_effect = [3, 0, 2, 0]
        staad_common.clean_staad()
        self.assertEqual(self.path.read_bytes(), self.empty_model)
        self.assertEqual(self.filename, str(self.path))

    def test_save_exception_propagates_before_reset(self):
        self.staad.SaveModel.side_effect = OSError("save failed")
        with self.assertRaisesRegex(OSError, "save failed"):
            staad_common.clean_staad()
        self.staad.CloseSTAADFile.assert_not_called()
        self.assertEqual(self.path.read_text(), self.original)


class ImportSafetyTests(unittest.TestCase):
    # agents.A6_tekla.tekla_open_api is not covered: it loads pythonnet and the Tekla
    # assemblies at import time by design.
    def test_staad_common_import_does_not_load_openstaadpy(self):
        with patch.dict(sys.modules, {"openstaadpy": None}):
            importlib.reload(staad_common)


if __name__ == "__main__":
    unittest.main()
