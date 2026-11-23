import sys
import unittest


@unittest.skipUnless(sys.platform == 'win32', 'Panneau Windows')
class PanelTabGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.qt = QApplication.instance() or QApplication(['panel-geometry', '-platform', 'offscreen'])

    def test_returning_to_count_restores_the_same_compact_rows(self):
        from PySide6.QtGui import QIcon

        from token_optimizer.panel import Panel
        from token_optimizer.workflow import Mode

        panel = Panel(QIcon(), motion=False)
        try:
            panel.show()
            panel.show_count(56)
            self.qt.processEvents()
            compact_widgets = (panel.header, panel.rail, panel.count_frame, panel.footer)
            starting_geometry = tuple(row.geometry().getRect() for row in compact_widgets)
            self.assertEqual(panel.height(), 262)
            self.assertEqual([rect[1] for rect in starting_geometry], [9, 46, 88, 239])

            for expanded in (False, True, False):
                panel.tabs[Mode.OPTIMIZE].click()
                if panel.scroll.isVisible() != expanded:
                    panel.expand.click()
                self.qt.processEvents()
                self.assertEqual(panel.scroll.isVisible(), expanded)
                if expanded:
                    self.assertGreater(panel.height(), 262)
                else:
                    self.assertEqual(panel.height(), 222)
                panel.tabs[Mode.COUNT].click()
                self.qt.processEvents()
                self.assertFalse(panel.scroll.isVisible())
                self.assertEqual(panel.height(), 262)
                self.assertEqual(tuple(row.geometry().getRect() for row in compact_widgets), starting_geometry)
                self.assertEqual(panel.count_ring.accessibleName(), '56 tokens estimés')
        finally:
            panel.close()
