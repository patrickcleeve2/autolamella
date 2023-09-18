import logging
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import napari
from napari.qt.threading import thread_worker
import yaml
from fibsem import utils
from fibsem import patterning
from fibsem.microscope import FibsemMicroscope
from fibsem.structures import (
    MicroscopeSettings,
    FibsemStagePosition, Point
)
from fibsem.ui.FibsemImageSettingsWidget import FibsemImageSettingsWidget
from fibsem.ui.FibsemMovementWidget import FibsemMovementWidget
from fibsem.ui.FibsemSystemSetupWidget import FibsemSystemSetupWidget
from fibsem.ui.FibsemMillingWidget import FibsemMillingWidget
from fibsem.ui.FibsemEmbeddedDetectionWidget import FibsemEmbeddedDetectionUI
from fibsem.ui.FibsemCryoSputterWidget import FibsemCryoSputterWidget
from fibsem.ui.FibsemMinimapWidget import FibsemMinimapWidget
from fibsem.ui import utils as fui
from fibsem.imaging._tile import _minimap 
from qtpy import QtWidgets

from autolamella.ui import utils as aui_utils
import autolamella.config as cfg
from autolamella.structures import (
    AutoLamellaWaffleStage,
    Experiment,
    Lamella,
    LamellaState,
)
from autolamella.ui.qt import AutoLamellaUI
from PyQt5.QtCore import pyqtSignal
from autolamella.ui import _stylesheets
from collections import Counter


_DEV_MODE = False
DEV_EXP_PATH = "/home/patrick/github/autolamella/autolamella/log/TEST_DEV_FEEDBACK_01/experiment.yaml"
DEV_PROTOCOL_PATH = cfg.PROTOCOL_PATH



class AutoLamellaUI(QtWidgets.QMainWindow, AutoLamellaUI.Ui_MainWindow):
    ui_signal = pyqtSignal(dict)
    det_confirm_signal = pyqtSignal(bool)
    update_experiment_signal = pyqtSignal(Experiment)
    _run_milling_signal = pyqtSignal()
    _minimap_signal = pyqtSignal(object)
    
    def __init__(self, viewer: napari.Viewer) -> None:
        super(AutoLamellaUI, self).__init__()
        
        self.setupUi(self)

        self.viewer = viewer
        self.viewer.window._qt_viewer.dockLayerList.setVisible(False)
        self.viewer.window._qt_viewer.dockLayerControls.setVisible(False)

        logging.info(f"INIT | INITIALISATION | STARTED")

        self._PROTOCOL_LOADED = False
        self._microscope_ui_loaded = False

        self.experiment: Experiment = None
        self.worker = None
        self.minimap_image = None
        self.microscope: FibsemMicroscope = None
        self.settings: MicroscopeSettings = None

        self.system_widget = FibsemSystemSetupWidget(
            microscope=self.microscope,
            settings=self.settings,
            viewer=self.viewer,
            config_path=cfg.SYSTEM_PATH,
        )
        self.tabWidget.addTab(self.system_widget, "System")

        self.image_widget: FibsemImageSettingsWidget = None
        self.movement_widget: FibsemMovementWidget = None
        self.milling_widget: FibsemMillingWidget = None
        self.minimap_widget: FibsemMinimapWidget = None

        self.WAITING_FOR_USER_INTERACTION: bool = False
        self.USER_RESPONSE: bool = False
        self.WAITING_FOR_UI_UPDATE: bool = False
        self._MILLING_RUNNING: bool = False
        self._WORKFLOW_RUNNING: bool = False



        # setup connections
        self.setup_connections()

        self.update_ui()

        if _DEV_MODE:
            self._auto_load()

        logging.info(f"INIT | INITIALISATION | FINISHED")


    def setup_connections(self):
        self.pushButton_add_lamella.clicked.connect(lambda: self.add_lamella_ui(pos=None))
        self.pushButton_add_lamella.setEnabled(False)
        self.pushButton_remove_lamella.clicked.connect(self.remove_lamella_ui)
        self.pushButton_remove_lamella.setEnabled(False)
        self.pushButton_go_to_lamella.clicked.connect(self.go_to_lamella_ui)
        self.pushButton_go_to_lamella.setEnabled(False)
        self.comboBox_current_lamella.currentIndexChanged.connect(self.update_lamella_ui)
        self.pushButton_save_position.clicked.connect(self.save_lamella_ui)
        self.pushButton_fail_lamella.clicked.connect(self.fail_lamella_ui)
        self.pushButton_revert_stage.clicked.connect(self.revert_stage)

        self.pushButton_run_waffle_trench.clicked.connect(self._run_trench_workflow)
        self.pushButton_run_autolamella.clicked.connect(self._run_lamella_workflow)
        self.pushButton_run_waffle_undercut.clicked.connect(self._run_undercut_workflow)
        self.pushButton_run_setup_autolamella.clicked.connect(self._run_setup_lamella_workflow)

        self.pushButton_run_waffle_trench.setVisible(False)
        self.pushButton_run_autolamella.setVisible(False)
        self.pushButton_run_waffle_undercut.setVisible(False)
        self.pushButton_run_setup_autolamella.setVisible(False)

        self.pushButton_update_protocol.clicked.connect(self.export_protocol_ui)
  
        # system widget
        self.system_widget.set_stage_signal.connect(self.set_stage_parameters)
        self.system_widget.connected_signal.connect(self.connect_to_microscope)
        self.system_widget.disconnected_signal.connect(self.disconnect_from_microscope)

        # file menu
        self.actionNew_Experiment.triggered.connect(self.setup_experiment)
        self.actionLoad_Experiment.triggered.connect(self.setup_experiment)
        self.actionLoad_Protocol.triggered.connect(self.load_protocol)
        self.actionSave_Protocol.triggered.connect(self.export_protocol_ui)
        self.actionCryo_Sputter.triggered.connect(self._cryo_sputter)
        self.actionLoad_Positions.triggered.connect(self._load_positions)
        self.actionOpen_Minimap.triggered.connect(self._open_minimap)
        self.actionLoad_Minimap_Image.triggered.connect(self.load_minimap_image)


        self.pushButton_yes.clicked.connect(self.push_interaction_button)
        self.pushButton_no.clicked.connect(self.push_interaction_button)

        # signals
        self.det_confirm_signal.connect(self._confirm_det)
        self.update_experiment_signal.connect(self._update_experiment)
        self.ui_signal.connect(self._ui_signal)
        self._run_milling_signal.connect(self._run_milling)

        self.pushButton_add_lamella.setStyleSheet(_stylesheets._GREEN_PUSHBUTTON_STYLE)
        self.pushButton_remove_lamella.setStyleSheet(_stylesheets._RED_PUSHBUTTON_STYLE)
        self.pushButton_go_to_lamella.setStyleSheet(_stylesheets._BLUE_PUSHBUTTON_STYLE)

        # comboboxes
        self.comboBox_method.addItems(["Default", "Waffle"])
        self.comboBox_stress_relief.addItems(["Notch","Microexpansion"])
        self.comboBox_current_alignment.addItems(["Imaging Current","Milling Current"])
        self.comboBox_alignment_with.addItems(["Fiducial", "No Fiducial"])

    def update_protocol_ui(self):

        if self._PROTOCOL_LOADED is False:
            return
        
        self.lineEdit_name.setText(self.settings.protocol["name"])

        self.beamshift_attempts.setValue(self.settings.protocol["lamella"]["beam_shift_attempts"])


        if self.settings.protocol["lamella"]["alignment_current"] in ["Imaging Current","Imaging"]:
            self.comboBox_current_alignment.setCurrentIndex(0)
        elif self.settings.protocol["lamella"]["alignment_current"] in ["Milling Current","Milling"]:
            self.comboBox_current_alignment.setCurrentIndex(1)

        self.doubleSpinBox_undercut_tilt.setValue(self.settings.protocol["undercut"]["tilt_angle"])
        self.doubleSpinBox_undercut_step.setValue(self.settings.protocol["undercut"]["tilt_angle_step"])

        self.comboBox_stress_relief.setCurrentIndex(0) if self.settings.protocol["notch"]["enabled"] else self.comboBox_stress_relief.setCurrentIndex(1)

        self.comboBox_method.setCurrentIndex(1) if self.settings.protocol["method"] == "waffle" else self.comboBox_method.setCurrentIndex(0)

        self.comboBox_alignment_with.setCurrentIndex(0) if self.settings.protocol["fiducial"]["enabled"] else self.comboBox_alignment_with.setCurrentIndex(1)

        # supervision

        self.checkBox_trench.setChecked(self.settings.protocol["options"]["supervise"]["trench"])
        self.checkBox_undercut.setChecked(self.settings.protocol["options"]["supervise"]["undercut"])
        self.checkBox_setup.setChecked(self.settings.protocol["options"]["supervise"]["setup_lamella"])
        self.checkBox_features.setChecked(self.settings.protocol["options"]["supervise"]["features"])
        self.checkBox_supervise_mill_rough.setChecked(self.settings.protocol["options"]["supervise"]["mill_rough"])
        self.checkBox_supervise_mill_regular.setChecked(self.settings.protocol["options"]["supervise"]["mill_regular"])
        self.checkBox_supervise_mill_polishing.setChecked(self.settings.protocol["options"]["supervise"]["mill_polishing"])

        # machine learning
        self.lineEdit_ml_checkpoint.setText(self.settings.protocol["ml"]["checkpoint"])
        self.lineEdit_ml_encoder.setText(self.settings.protocol["ml"]["encoder"])


    def export_protocol_ui(self):

        if self._PROTOCOL_LOADED is False:
            return
        self.settings.protocol["name"] = self.lineEdit_name.text()
        self.settings.protocol["lamella"]["beam_shift_attempts"] = self.beamshift_attempts.value()
        self.settings.protocol["lamella"]["alignment_current"] = self.comboBox_current_alignment.currentText()
        self.settings.protocol["undercut"]["tilt_angle"] = self.doubleSpinBox_undercut_tilt.value()
        self.settings.protocol["undercut"]["tilt_angle_step"] = self.doubleSpinBox_undercut_step.value()
        self.settings.protocol["notch"]["enabled"] = bool(self.comboBox_stress_relief.currentIndex() == 0)
        self.settings.protocol["fiducial"]["enabled"] = bool(self.comboBox_alignment_with.currentIndex() == 0)
        self.settings.protocol["method"] = self.comboBox_method.currentText().lower()

        # supervision

        self.settings.protocol["options"]["supervise"]["trench"] = self.checkBox_trench.isChecked()
        self.settings.protocol["options"]["supervise"]["undercut"] = self.checkBox_undercut.isChecked()
        self.settings.protocol["options"]["supervise"]["setup_lamella"] = self.checkBox_setup.isChecked()
        self.settings.protocol["options"]["supervise"]["features"] = self.checkBox_features.isChecked()
        self.settings.protocol["options"]["supervise"]["mill_rough"] = self.checkBox_supervise_mill_rough.isChecked()
        self.settings.protocol["options"]["supervise"]["mill_regular"] = self.checkBox_supervise_mill_regular.isChecked()
        self.settings.protocol["options"]["supervise"]["mill_polishing"] = self.checkBox_supervise_mill_polishing.isChecked()

        # machine learning
        self.settings.protocol["ml"]["checkpoint"] = self.lineEdit_ml_checkpoint.text()
        self.settings.protocol["ml"]["encoder"] = self.lineEdit_ml_encoder.text()

        if self.sender() == self.actionSave_Protocol:
            path = fui._get_save_file_ui(msg='Save protocol',
                path = cfg.PROTOCOL_PATH,
                _filter= "*yaml",
                parent=self)
            utils.save_yaml(path, self.settings.protocol)
            
            logging.info("Protocol saved to file")
        elif self.sender() == self.pushButton_update_protocol:
            logging.info("Protocol updated")

        # auto save copy to experiment folder        
        if self.experiment:
            utils.save_yaml(os.path.join(self.experiment.path, "protocol.yaml"), self.settings.protocol) # Q: do we really wanna overwrite this file?

        napari.utils.notifications.show_info(f"Protocol updated.")
        self.update_ui()



    def setup_experiment(self):
        new_experiment = bool(self.sender() is self.actionNew_Experiment)
        experiment = aui_utils.setup_experiment_ui_v2(
            self, new_experiment=new_experiment
        )

        if experiment is None:
            napari.utils.notifications.show_info(f"Experiment not loaded.")
            return

        self.experiment = experiment
        napari.utils.notifications.show_info(
            f"Experiment {self.experiment.name} loaded."
        )
        if self.settings is not None:
            self.settings.image.save_path = self.experiment.path


        # register metadata
        if cfg._REGISTER_METADATA:
            import autolamella
            utils._register_metadata(
                microscope=self.microscope,
                parent_type="autolamella",
                parent_version=autolamella.__version__,
                parent_ui=self,
                experiment_name = self.experiment.name,
            )

        self._update_lamella_combobox()
        self.update_ui()


    ##################################################################

    # TODO: move this to system wideget??
    def connect_to_microscope(self):
        self.microscope = self.system_widget.microscope
        self.settings = self.system_widget.settings
        if self.experiment is not None:
            self.settings.image.save_path = self.experiment.path
        self.update_microscope_ui()
        self.update_ui()

    def disconnect_from_microscope(self):
        self.microscope = None
        self.settings = None
        self.update_microscope_ui()
        self.update_ui()

    def set_stage_parameters(self):
        if self.microscope is None:
            return
        self.settings.system.stage = (
            self.system_widget.settings.system.stage
        )  # TODO: this doesnt actually update the movement widget
        logging.debug(f"Stage parameters set to {self.settings.system.stage}")
        logging.info("Stage parameters set")

    def update_microscope_ui(self):
        """Update the ui based on the current state of the microscope."""

        if self.microscope is not None and not self._microscope_ui_loaded:
            # reusable components
            self.image_widget = FibsemImageSettingsWidget(
                microscope=self.microscope,
                image_settings=self.settings.image,
                viewer=self.viewer,
                parent=self,
            )
            self.movement_widget = FibsemMovementWidget(
                microscope=self.microscope,
                settings=self.settings,
                viewer=self.viewer,
                image_widget=self.image_widget,
                parent=self,
            )
            self.milling_widget = FibsemMillingWidget(
                microscope=self.microscope,
                settings=self.settings,
                viewer=self.viewer,
                image_widget=self.image_widget,
                parent=self,
            )

            self.det_widget = FibsemEmbeddedDetectionUI(
                viewer=self.viewer, 
                model=None,
                )

            # add widgets to tabs
            self.tabWidget.addTab(self.image_widget, "Image")
            self.tabWidget.addTab(self.movement_widget, "Movement")
            self.tabWidget.addTab(self.milling_widget, "Milling")
            self.tabWidget.addTab(self.det_widget, "Detection")

            self._microscope_ui_loaded = True
            self.milling_widget.milling_position_changed.connect(self._update_milling_position)
            self.milling_widget._milling_finished.connect(self._milling_finished)

        else:
            if self.image_widget is None:
                return

            # remove tabs
            self.tabWidget.removeTab(6)
            self.tabWidget.removeTab(5)
            self.tabWidget.removeTab(4)
            self.tabWidget.removeTab(3)
            self.tabWidget.removeTab(3)

            self.image_widget.clear_viewer()
            self.image_widget.deleteLater()
            self.movement_widget.deleteLater()
            self.milling_widget.deleteLater()
            self.det_widget.deleteLater()

            self._microscope_ui_loaded = False

    def _open_minimap(self):
        if self.microscope is None:
            napari.utils.notifications.show_warning(f"Please connect to a microscope first... [No Microscope Connected]")
            return

        if self.movement_widget is None:
            napari.utils.notifications.show_warning(f"Please connect to a microscope first... [No Movement Widget]")
            return


        # TODO: should make this more generic i guess, but this is fine for now
        self.viewer2 = napari.Viewer(ndisplay=2)
        self.minimap_widget = FibsemMinimapWidget(self.microscope, deepcopy(self.settings), viewer=self.viewer2, parent=self)
        self.viewer2.window.add_dock_widget(
            self.minimap_widget, area="right", add_vertical_stretch=False, name="OpenFIBSEM Minimap"
        )
        self.minimap_widget._stage_position_moved.connect(self.movement_widget._stage_position_moved)
        self.minimap_widget._minimap_positions.connect(self.movement_widget.minimap_window_positions)
        self.minimap_widget._minimap_positions.connect(self.update_experiment_positions)
        

        positions = [lamella.state.microscope_state.absolute_position for lamella in self.experiment.positions]

        self.movement_widget.positions_signal.connect(self.minimap_connection)
        self.movement_widget.move_signal.connect(self.minimap_connection)
        self.minimap_connection(positions=positions)

        # self.minimap_widget.positions = positions
        # self.minimap_widget._update_position_info()
        # self.minimap_widget._update_viewer()

        if cfg._AUTO_SYNC_MINIMAP:
            self.minimap_widget._stage_position_added.connect(self._add_lamella_from_minimap)
        napari.run(max_loop_level=2)

    def update_experiment_positions(self,positions=None):

        if positions is None:
            return

        if len(positions) < len(self.experiment.positions):
            
            idx = 0
            position_names = [p.name for p in positions]
            # find index of missing position
            for lamella in self.experiment.positions:
                if lamella._petname not in position_names:
                    break
                idx += 1

            self.experiment.positions.pop(idx)
            self.experiment.save()
            self._update_lamella_combobox()
            self.update_ui()

        # modify exisitng position
        else:
            for idx, pos in enumerate(positions):
                self.experiment.positions[idx].state.microscope_state.absolute_position = pos
            self.experiment.save()
            self.update_ui()

            


    def minimap_connection(self,positions=None):

        if self.minimap_widget is None:
            return
        else:
            self._minimap_signal.emit(positions)

    # add lmaella from minimap
    def _add_lamella_from_minimap(self, position: FibsemStagePosition):

        self.add_lamella_ui(position)

        self._update_stage_positions()

    def _update_stage_positions(self):
        
        # add lamella to experiment from tile manager
        if self.experiment is None:
            logging.warning("No experiment loaded")
            return

        positions = [lamella.state.microscope_state.absolute_position for lamella in self.experiment.positions]

        self.minimap_connection(positions=positions)

    def _load_positions(self):
        
        
        path = fui._get_file_ui( msg="Select a position file to load", 
            path=self.experiment.path, 
            _filter= "*yaml", 
            parent=self)

        if path == "":
            napari.utils.notifications.show_info(f"No file selected..")
            return

        pdict = utils.load_yaml(path)
        
        positions = [FibsemStagePosition.__from_dict__(p) for p in pdict]

        for pos in positions:
            self.add_lamella_ui(pos)

    def update_ui(self):
        """Update the ui based on the current state of the application."""

        _experiment_loaded = bool(self.experiment is not None)
        _microscope_connected = bool(self.microscope is not None)
        _protocol_loaded = bool(self.settings is not None) and self._PROTOCOL_LOADED
        _lamella_selected = bool(self.experiment.positions) if _experiment_loaded else False

        # setup experiment -> connect to microscope -> select lamella -> run autolamella
        self.pushButton_fail_lamella.setVisible(_lamella_selected)
        self.pushButton_revert_stage.setVisible(_lamella_selected)
        self.comboBox_lamella_history.setVisible(_lamella_selected)
        
        # experiment loaded
        self.actionLoad_Protocol.setVisible(_experiment_loaded)
        self.actionSave_Protocol.setVisible(_protocol_loaded)
        self.actionCryo_Sputter.setVisible(_protocol_loaded)
        self.actionOpen_Minimap.setVisible(_protocol_loaded)
        self.actionLoad_Minimap_Image.setVisible(_protocol_loaded and cfg._MINIMAP_VISUALISATION)
        self.actionLoad_Positions.setVisible(_protocol_loaded)

        # labels
        if _experiment_loaded:
            self.label_experiment_name.setText(f"Experiment: {self.experiment.name}")

            msg = "\nLamella Info:\n"
            for lamella in self.experiment.positions:
                fmsg = '\t\t FAILED' if lamella._is_failure else ''
                msg += f"Lamella {lamella._petname} \t\t {lamella.state.stage.name} {fmsg} \n"            
            self.label_info.setText(msg)

            self.comboBox_current_lamella.setVisible(_lamella_selected)

        if _protocol_loaded:
            method = self.settings.protocol.get("method", "waffle")
            self.label_protocol_name.setText(
                f"Protocol: {self.settings.protocol.get('name', 'protocol')} ({method.title()} Method)"
            )

        # buttons
        self.pushButton_add_lamella.setEnabled(_protocol_loaded and _experiment_loaded)
        self.pushButton_remove_lamella.setEnabled(_lamella_selected)
        self.pushButton_save_position.setEnabled(_lamella_selected)
        self.pushButton_go_to_lamella.setEnabled(_lamella_selected)

        if _experiment_loaded and _protocol_loaded:
            # workflow buttons
            _WAFFLE_METHOD = self.settings.protocol.get("method", "waffle") == "waffle"
            _counter = Counter([p.state.stage.name for p in self.experiment.positions])
            
            _READY_TRENCH = _counter[AutoLamellaWaffleStage.ReadyTrench.name] > 0
            _READY_UNDERCUT = _counter[AutoLamellaWaffleStage.MillTrench.name] > 0
            _READY_LAMELLA = _counter[AutoLamellaWaffleStage.SetupLamella.name] > 0
            _READY_AUTOLAMELLA = _counter[AutoLamellaWaffleStage.ReadyLamella.name] > 0
            _READY_FEATURES = _counter[AutoLamellaWaffleStage.MillFeatures.name] > 0
            _READY_ROUGH = _counter[AutoLamellaWaffleStage.MillRoughCut.name] > 0
            _READY_REGULAR = _counter[AutoLamellaWaffleStage.MillRegularCut.name] > 0
            _READY_AUTOLAMELLA = _READY_AUTOLAMELLA or _READY_FEATURES or _READY_ROUGH or _READY_REGULAR

            _ENABLE_TRENCH = _WAFFLE_METHOD and _READY_TRENCH
            _ENABLE_UNDERCUT = _WAFFLE_METHOD and _READY_UNDERCUT
            _ENABLE_LAMELLA = _READY_LAMELLA
            _ENABLE_AUTOLAMELLA = _READY_AUTOLAMELLA


            self.pushButton_run_waffle_trench.setVisible(_WAFFLE_METHOD)
            self.pushButton_run_waffle_trench.setEnabled(_ENABLE_TRENCH)
            self.pushButton_run_waffle_undercut.setVisible(_WAFFLE_METHOD)
            self.pushButton_run_waffle_undercut.setEnabled(_ENABLE_UNDERCUT)
            self.pushButton_run_setup_autolamella.setVisible(True)
            self.pushButton_run_setup_autolamella.setEnabled(_ENABLE_LAMELLA)
            self.pushButton_run_autolamella.setVisible(True)
            self.pushButton_run_autolamella.setEnabled(_ENABLE_AUTOLAMELLA)


            self.pushButton_run_waffle_trench.setStyleSheet(_stylesheets._GREEN_PUSHBUTTON_STYLE if _ENABLE_TRENCH else _stylesheets._DISABLED_PUSHBUTTON_STYLE)
            self.pushButton_run_waffle_undercut.setStyleSheet(_stylesheets._GREEN_PUSHBUTTON_STYLE if _ENABLE_UNDERCUT else _stylesheets._DISABLED_PUSHBUTTON_STYLE)
            self.pushButton_run_setup_autolamella.setStyleSheet(_stylesheets._GREEN_PUSHBUTTON_STYLE if _ENABLE_LAMELLA else _stylesheets._DISABLED_PUSHBUTTON_STYLE)
            self.pushButton_run_autolamella.setStyleSheet(_stylesheets._GREEN_PUSHBUTTON_STYLE if _ENABLE_AUTOLAMELLA else _stylesheets._DISABLED_PUSHBUTTON_STYLE)

        # Current Lamella Status
        if _lamella_selected:
            self.update_lamella_ui()

        # instructions# TODO: update with autolamella instructions
        INSTRUCTIONS = {"NOT_CONNECTED": "Please connect to the microscope (System -> Connect to Microscope).",
                        "NO_EXPERIMENT": "Please create or load an experiment (File -> Create / Load Experiment)",
                        "NO_PROTOCOL": "Please load a protocol (File -> Load Protocol).",
                        "NO_LAMELLA": "Please Add Lamella Positions (Experiment -> Add Lamella).",
                        "TRENCH_READY": "Trench Positions Selected. Ready to Run Waffle Trench.",
                        "UNDERCUT_READY": "Undercut Positions Selected. Ready to Run Waffle Undercut.",
                        "LAMELLA_READY": "Lamella Positions Selected. Ready to Run Setup AutoLamella.",
                        "AUTOLAMELLA_READY": "Lamella Positions Selected. Ready to Run AutoLamella.",
                        }

        if not _microscope_connected:
            self._set_instructions(INSTRUCTIONS["NOT_CONNECTED"])
        elif not _experiment_loaded:
            self._set_instructions(INSTRUCTIONS["NO_EXPERIMENT"])
        elif not _protocol_loaded:
            self._set_instructions(INSTRUCTIONS["NO_PROTOCOL"])
        elif not _lamella_selected:
            self._set_instructions(INSTRUCTIONS["NO_LAMELLA"])
        elif _lamella_selected:
            self._set_instructions(INSTRUCTIONS["AUTOLAMELLA_READY"])

        if self.experiment is not None and self.minimap_image is not None:
            positions = []
            for lamella in self.experiment.positions:
                position = lamella.state.microscope_state.absolute_position
                position.name = lamella._petname
                positions.append(position)
            
            qpixmap = _minimap(self.minimap_image, positions)

            self.label_minimap.setPixmap(qpixmap)

    def load_minimap_image(self):

        path = fui._get_file_ui( msg="Select image to load", 
                                path=self.experiment.path, _filter="Image Files (*.tif *.tiff)", parent=self)

        if path == "":
            napari.utils.notifications.show_info(f"No file selected..")
            return

        from fibsem.structures import FibsemImage
        image = FibsemImage.load(path)
        if image.metadata is None:
            napari.utils.notifications.show_error(f"Could not load image {path}. Make sure it is an OpenFibsem Image.")
            return

        self.minimap_image = image
        self.update_ui()

    def _update_lamella_combobox(self):
        # detail combobox
        idx = self.comboBox_current_lamella.currentIndex()
        self.comboBox_current_lamella.currentIndexChanged.disconnect()
        self.comboBox_current_lamella.clear()
        self.comboBox_current_lamella.addItems([lamella.info for lamella in self.experiment.positions])
        if idx != -1 and self.experiment.positions:
            self.comboBox_current_lamella.setCurrentIndex(idx)
        self.comboBox_current_lamella.currentIndexChanged.connect(self.update_lamella_ui)

    def update_lamella_ui(self):

        # set the info for the current selected lamella
        if self.experiment is None:
            return
        
        if self.experiment.positions == []:
            return      

        idx = self.comboBox_current_lamella.currentIndex()
        lamella: Lamella = self.experiment.positions[idx]

        logging.info(f"Updating Lamella UI for {lamella.info}")

        # buttons
        if self._PROTOCOL_LOADED:

            method = self.settings.protocol.get("method", "waffle")
            SETUP_STAGES =  [AutoLamellaWaffleStage.SetupTrench] if method == "waffle" else [AutoLamellaWaffleStage.PreSetupLamella]
            READY_STAGES = [AutoLamellaWaffleStage.ReadyTrench] if method == "waffle" else [AutoLamellaWaffleStage.SetupLamella]
            if lamella.state.stage in SETUP_STAGES:
                self.pushButton_save_position.setText(f"Save Position")
                self.pushButton_save_position.setStyleSheet(_stylesheets._ORANGE_PUSHBUTTON_STYLE)
                self.pushButton_save_position.setEnabled(True)
                self.milling_widget._PATTERN_IS_MOVEABLE = True
            elif lamella.state.stage in READY_STAGES:
                self.pushButton_save_position.setText(f"Position Ready")
                self.pushButton_save_position.setStyleSheet(_stylesheets._GREEN_PUSHBUTTON_STYLE)
                self.pushButton_save_position.setEnabled(True)
                self.milling_widget._PATTERN_IS_MOVEABLE = False
            else:
                self.pushButton_save_position.setText(f"")
                self.pushButton_save_position.setStyleSheet(_stylesheets._DISABLED_PUSHBUTTON_STYLE)
                self.pushButton_save_position.setEnabled(False)
                self.milling_widget._PATTERN_IS_MOVEABLE = True

        # update the milling widget
        if self._WORKFLOW_RUNNING:
            self.milling_widget._PATTERN_IS_MOVEABLE = True

        if lamella.state.stage in [AutoLamellaWaffleStage.SetupTrench, AutoLamellaWaffleStage.ReadyTrench, 
            AutoLamellaWaffleStage.SetupLamella, AutoLamellaWaffleStage.ReadyLamella, AutoLamellaWaffleStage.PreSetupLamella]:
            
            if self._PROTOCOL_LOADED:

                _DISPLAY_TRENCH, _DISPLAY_LAMELLA = False, False
                method = self.settings.protocol.get("method", "waffle")
                
                if method == "waffle" and lamella.state.stage in [AutoLamellaWaffleStage.SetupTrench, AutoLamellaWaffleStage.ReadyTrench]:
                    _DISPLAY_TRENCH = True

                # show lamella and friends
                if lamella.state.stage in [AutoLamellaWaffleStage.SetupLamella, AutoLamellaWaffleStage.ReadyLamella, AutoLamellaWaffleStage.PreSetupLamella]:
                    _DISPLAY_TRENCH, _DISPLAY_LAMELLA = False, True


                if _DISPLAY_TRENCH:
                    protocol = lamella.protocol if "trench" in lamella.protocol else self.settings.protocol
                    trench_position = Point.__from_dict__(protocol["trench"].get("point", {"x": 0, "y": 0})) 
                    stages = patterning._get_milling_stages("trench", protocol, trench_position)

                if _DISPLAY_LAMELLA:
                    protocol = lamella.protocol if "lamella" in lamella.protocol else self.settings.protocol
                    lamella_position = Point.__from_dict__(protocol["lamella"].get("point", {"x": 0, "y": 0})) 
                    stages = patterning._get_milling_stages("lamella", protocol, lamella_position)
                    
                    _feature_name = "notch" if self.settings.protocol["notch"]["enabled"]  else "microexpansion"
                    protocol = lamella.protocol if _feature_name in lamella.protocol else self.settings.protocol
                    
                    NOTCH_H_OFFSET = 0.5e-6                     
                    feature_position = Point.__from_dict__(protocol[_feature_name].get("point", 
                            {"x":lamella_position.x + stages[0].pattern.protocol["lamella_width"] / 2 + NOTCH_H_OFFSET, 
                            "y": lamella_position.y} if _feature_name == "notch" else {"x": 0, "y": 0})) 
                    feature_stage = patterning._get_milling_stages(_feature_name, protocol, feature_position)
                    stages += feature_stage

                    # fiducial
                    if self.settings.protocol["fiducial"]["enabled"]:
                        protocol = lamella.protocol if "fiducial" in lamella.protocol else self.settings.protocol
                        fiducial_position = Point.__from_dict__(protocol["fiducial"].get("point", {"x": 25e-6, "y": 0})) 
                        fiducial_stage = patterning._get_milling_stages("fiducial", protocol, fiducial_position)
                        stages += fiducial_stage

                self.milling_widget.set_milling_stages(stages)

        if lamella._is_failure:
            self.pushButton_fail_lamella.setText("Mark Lamella as Active")
        else:
            self.pushButton_fail_lamella.setText("Mark Lamella As Failed")

        def _to_str(state: LamellaState):
            return f"{state.stage.name} ({datetime.fromtimestamp(state.end_timestamp).strftime('%I:%M%p')})"
        
        self.comboBox_lamella_history.clear()
        _lamella_history = bool(lamella.history)
        self.comboBox_lamella_history.setVisible(_lamella_history)
        self.pushButton_revert_stage.setVisible(_lamella_history)
        self.comboBox_lamella_history.addItems([_to_str(state) for state in lamella.history])

    def _update_milling_position(self):
        # triggered when milling position is moved
        if self.experiment is None:
            return
        
        if self.experiment.positions == []:
            return

        idx = self.comboBox_current_lamella.currentIndex()
        lamella: Lamella = self.experiment.positions[idx]

        if lamella.state.stage not in  [AutoLamellaWaffleStage.SetupTrench, AutoLamellaWaffleStage.SetupLamella, AutoLamellaWaffleStage.PreSetupLamella]:
            return

        logging.info(f"Updating Lamella Pattern for {lamella.info}")

        # update the trench point
        method = self.settings.protocol.get("method", "waffle")
        self._update_milling_protocol(idx=idx, method=method, stage=lamella.state.stage)

        self.experiment.save() 


    def load_protocol(self):
        """Load a protocol from file."""

        if self.settings is None:
            napari.utils.notifications.show_info(
                f"Please connect to the microscope first."
            )
            return

        PATH = fui._get_file_ui(
            msg="Select a protocol file", path=cfg.PROTOCOL_PATH, parent=self
        )

        if PATH == "":
            napari.utils.notifications.show_info(f"No path selected")
            logging.info("No path selected")
            return

        self.settings.protocol = utils.load_protocol(protocol_path=PATH)
        self._PROTOCOL_LOADED = True
        self.update_protocol_ui()
        napari.utils.notifications.show_info(
            f"Loaded Protocol from {os.path.basename(PATH)}"
        )

        # save a copy of the protocol to the experiment.path
        if self.experiment:
            utils.save_yaml(os.path.join(self.experiment.path, "protocol.yaml"), self.settings.protocol)

        self.update_ui()

    def _cryo_sputter(self):

        cryo_sputter_widget = FibsemCryoSputterWidget(self.microscope, self.settings)
        cryo_sputter_widget.exec_()

    def save_protocol(self):
        fname = fui._get_save_file_ui(msg="Select protocol file", path=cfg.LOG_PATH)
        if fname == "":
            return

        # give protocol path as suffix .yaml if not
        fname = Path(fname).with_suffix(".yaml")

        with open(os.path.join(fname), "w") as f:
            yaml.safe_dump(self.settings.protocol, f, indent=4)

        logging.info("Protocol saved to file")


    def _set_instructions(
        self, msg: str = "", pos: str = None, neg: str = None,
    ):

        self.label_instructions.setText(msg)
        self.pushButton_yes.setText(pos)
        self.pushButton_no.setText(neg)
        
        # enable buttons
        self.pushButton_yes.setEnabled(pos is not None)
        self.pushButton_no.setEnabled(neg is not None)
        self.pushButton_yes.setVisible(pos is not None)
        self.pushButton_no.setVisible(neg is not None)

    def push_interaction_button(self):
        logging.info("Sender: {}".format(self.sender().objectName()))

        # positve / negative response
        self.USER_RESPONSE = bool(self.sender() == self.pushButton_yes)
        self.WAITING_FOR_USER_INTERACTION = False


    def _auto_load(self):

        # connect to microscope
        self.system_widget.connect_to_microscope()

        # load experiment
        self.experiment = Experiment.load(DEV_EXP_PATH)
        self.settings.image.save_path = self.experiment.path
        self._update_lamella_combobox()

        # load protocol
        self.settings.protocol = utils.load_protocol(protocol_path=DEV_PROTOCOL_PATH)
        self._PROTOCOL_LOADED = True
        self.update_protocol_ui()

        self.update_ui()
        return 

    def go_to_lamella_ui(self):
        print("go to lamella ui")
        
        idx = self.comboBox_current_lamella.currentIndex()
        lamella: Lamella = self.experiment.positions[idx]
        position = lamella.state.microscope_state.absolute_position

        self.movement_widget.go_to_saved_position(position)
        logging.info(f"Moved to position of {lamella.info}.")

    def add_lamella_ui(self, pos:FibsemStagePosition=None):

        method = self.settings.protocol.get("method", "waffle")
        stage = AutoLamellaWaffleStage.SetupTrench if method == "waffle" else AutoLamellaWaffleStage.PreSetupLamella

        lamella = Lamella(
            path=self.experiment.path,
            _number=len(self.experiment.positions) + 1,
            state=LamellaState(
                stage=stage,
                microscope_state=self.microscope.get_current_microscope_state(),
                start_timestamp = datetime.timestamp(datetime.now())
        ))
        from autolamella.workflows.core import log_status_message
        log_status_message(lamella, "STARTED")
        
        if pos is not None:
            lamella.state.microscope_state.absolute_position = deepcopy(pos)

            lamella.state.microscope_state.absolute_position.name = lamella._petname

        self.experiment.positions.append(deepcopy(lamella))
        
        self.experiment.save()

        logging.info(f"Added lamella {lamella._petname} to experiment {self.experiment.name}.")

        self._update_lamella_combobox()
        self.update_ui()
    
    def remove_lamella_ui(self):

        idx = self.comboBox_current_lamella.currentIndex()
        self.experiment.positions.pop(idx)
        self.experiment.save()

        logging.info("Lamella removed from experiment")
        self._update_lamella_combobox()
        self.update_ui()

        self._update_stage_positions() # update the minimap

    def fail_lamella_ui(self):
        idx = self.comboBox_current_lamella.currentIndex()
        self.experiment.positions[idx]._is_failure = True if not self.experiment.positions[idx]._is_failure else False
        self.experiment.save()
        self.update_ui()

    def revert_stage(self):
        idx = self.comboBox_current_lamella.currentIndex()
        hidx = self.comboBox_lamella_history.currentIndex()
        
        self.experiment.positions[idx].state = deepcopy(self.experiment.positions[idx].history[hidx])
        self.experiment.positions[idx].state.start_timestamp = datetime.timestamp(datetime.now())
        from autolamella.workflows.core import log_status_message
        log_status_message(self.experiment.positions[idx], "STARTED")
        # TODO: use start of stage update to restore the state properly

        self.update_ui()

    def save_lamella_ui(self):
        # triggered when save button is pressed

        if self.experiment.positions == []:
            return

        idx = self.comboBox_current_lamella.currentIndex()
        # TOGGLE BETWEEN READY AND SETUP

        method = self.settings.protocol.get("method", "waffle")
        SETUP_STATE = AutoLamellaWaffleStage.SetupTrench if method == "waffle" else AutoLamellaWaffleStage.PreSetupLamella
        READY_STATE = AutoLamellaWaffleStage.ReadyTrench if method == "waffle" else AutoLamellaWaffleStage.SetupLamella
        
        # if waffle, but at setuplamella: 
        # SETUP_STATE = AutoLamellaWaffleStage.SetupLamella
        # READY_STATE = AutoLamellaWaffleStage.ReadyLamella

        lamella: Lamella = self.experiment.positions[idx]
        from autolamella import waffle as wfl

        if lamella.state.stage not in [SETUP_STATE, READY_STATE]:
            return

        # end of stage update
        self.experiment = wfl.end_of_stage_update(
            microscope=self.microscope, 
            experiment=self.experiment, 
            lamella=lamella, 
            parent_ui=self, 
            _save_state=True
        ) 
        
        if lamella.state.stage is SETUP_STATE:   

            # start of stage update
            self.experiment.positions[idx] = wfl.start_of_stage_update(
                microscope=self.microscope, 
                lamella=lamella, 
                next_stage=READY_STATE, parent_ui=self, 
                _restore_state=False) 

            # update the protocol / point
            self._update_milling_protocol(idx, method, READY_STATE)

            # get current ib image, save as reference
            fname = os.path.join(
                self.experiment.positions[idx].path, 
                f"ref_{self.experiment.positions[idx].state.stage.name}"
            )
            self.image_widget.ib_image.save(fname)
            self.milling_widget._PATTERN_IS_MOVEABLE = False

            self.experiment = wfl.end_of_stage_update(
                microscope=self.microscope, 
                experiment=self.experiment, 
                lamella=lamella, 
                parent_ui=self, 
                _save_state=True
            ) 

        elif (lamella.state.stage is READY_STATE):

            self.experiment.positions[idx] = wfl.start_of_stage_update(
                self.microscope, 
                lamella, 
                SETUP_STATE, 
                parent_ui=self, 
                _restore_state=False) 

            self.milling_widget._PATTERN_IS_MOVEABLE = True

        lamella.state.microscope_state.absolute_position.name = lamella._petname


        if self.minimap_widget is not None:
            positions = [lamella.state.microscope_state.absolute_position for lamella in self.experiment.positions]
            self.minimap_connection(positions=positions)


        self._update_lamella_combobox()
        self.update_ui()
        self.experiment.save()

    def _update_milling_protocol(self, idx: int, method: str, stage: AutoLamellaWaffleStage):

        stages = deepcopy(self.milling_widget.get_milling_stages())
        if method == "waffle" and stage in [AutoLamellaWaffleStage.SetupTrench, AutoLamellaWaffleStage.ReadyTrench]:
            self.experiment.positions[idx].protocol["trench"] = deepcopy(patterning._get_protocol_from_stages(stages))
            self.experiment.positions[idx].protocol["trench"]["point"] = stages[0].pattern.point.__to_dict__()
        
        if stage in [AutoLamellaWaffleStage.SetupLamella, AutoLamellaWaffleStage.PreSetupLamella]:
            n_lamella = len(self.settings.protocol["lamella"]["stages"])

            # lamella
            self.experiment.positions[idx].protocol["lamella"] = deepcopy(patterning._get_protocol_from_stages(stages[:n_lamella]))
            self.experiment.positions[idx].protocol["lamella"]["point"] = stages[0].pattern.point.__to_dict__()

            # feature
            _feature_name = "notch" if self.settings.protocol["notch"]["enabled"] else "microexpansion"
            self.experiment.positions[idx].feature_position = stages[n_lamella].pattern.point
            self.experiment.positions[idx].protocol[_feature_name] = deepcopy(patterning._get_protocol_from_stages(stages[n_lamella]))
            self.experiment.positions[idx].protocol[_feature_name]["point"] = stages[n_lamella].pattern.point.__to_dict__()

            
            # fiducial (optional)
            if self.settings.protocol["fiducial"]["enabled"]:
                self.experiment.positions[idx].fiducial_centre = stages[-1].pattern.point
                self.experiment.positions[idx].protocol["fiducial"] = deepcopy(patterning._get_protocol_from_stages(stages[-1]))
                self.experiment.positions[idx].protocol["fiducial"]["point"] = stages[-1].pattern.point.__to_dict__()



    def _run_milling(self):
        self._MILLING_RUNNING = True
        self.milling_widget.run_milling()

    def _milling_finished(self):
        self._MILLING_RUNNING = False

    def _confirm_det(self):
        if self.det_widget is not None:
            self.det_widget.confirm_button_clicked()

    def _run_trench_workflow(self):
        try:
            self.milling_widget.milling_position_changed.disconnect()
        except:
            pass
        
        self.worker = self._threaded_worker(
            microscope=self.microscope, settings=self.settings, experiment=self.experiment, workflow="trench",
        )
        self.worker.finished.connect(self._workflow_finished)
        self.worker.start()

    def _run_undercut_workflow(self):
        try:
            self.milling_widget.milling_position_changed.disconnect()
        except:
            pass
        self.worker = self._threaded_worker(
            microscope=self.microscope, settings=self.settings, experiment=self.experiment, workflow="undercut",
        )
        self.worker.finished.connect(self._workflow_finished)
        self.worker.start()
    
    def _run_setup_lamella_workflow(self):
        try:
            self.milling_widget.milling_position_changed.disconnect()
        except:
            pass
        self.worker = self._threaded_worker(
            microscope=self.microscope, settings=self.settings, experiment=self.experiment, workflow="setup-lamella",
        )
        self.worker.finished.connect(self._workflow_finished)
        self.worker.start()

    # TODO: consolidate all these diff workflow functions
    def _run_lamella_workflow(self): 
        try:
            self.milling_widget.milling_position_changed.disconnect()
        except:
            pass 

        self.worker = self._threaded_worker(
            microscope=self.microscope, settings=self.settings, experiment=self.experiment, workflow="lamella",
        )
        self.worker.finished.connect(self._workflow_finished)
        self.worker.start()


    def _workflow_finished(self):
        logging.info(f'Workflow finished.')
        self._WORKFLOW_RUNNING = False
        self.milling_widget.milling_position_changed.connect(self._update_milling_position)
        self.tabWidget.setCurrentIndex(0)

    def _ui_signal(self, info:dict) -> None:
        """Update the UI with the given information, ready for user interaction"""
        _mill = bool(info["mill"] is not None) if info["mill"] is None else info["mill"]
        _det = bool(info["det"] is not None)
        stages = info.get("stages", None)


        if _det:
            self.det_widget.set_detected_features(info["det"])
            self.tabWidget.setCurrentIndex(6)
        
        if _mill:
            self.tabWidget.setCurrentIndex(5)

        if info["eb_image"] is not None:
            eb_image = info["eb_image"]
            self.image_widget.eb_image = eb_image
            self.image_widget.update_viewer(eb_image.data, "ELECTRON", _set_ui=True)
        if info["ib_image"] is not None:
            ib_image = info["ib_image"]
            self.image_widget.ib_image = ib_image
            self.image_widget.update_viewer(ib_image.data, "ION", _set_ui=True)


        if isinstance(stages, list):
            self.milling_widget.set_milling_stages(stages)
        if stages == "clear":
            self.milling_widget._remove_all_stages()

        # ui interaction
        self.milling_widget.pushButton_run_milling.setEnabled(_mill)
        self.milling_widget.pushButton_run_milling.setVisible(_mill)

        # instruction message
        self._set_instructions(info["msg"], info["pos"], info["neg"])

        self.WAITING_FOR_UI_UPDATE = False

    def _update_experiment(self, experiment: Experiment):
        self.experiment = experiment
        self._update_lamella_combobox()
        self.update_ui()

    @thread_worker
    def _threaded_worker(self, microscope: FibsemMicroscope, settings: MicroscopeSettings, experiment: Experiment, workflow: str="trench"):
        
        self._WORKFLOW_RUNNING = True
        self.milling_widget._PATTERN_IS_MOVEABLE = True
        self.milling_widget._remove_all_stages()
        self.WAITING_FOR_USER_INTERACTION = False

        self._set_instructions(f"Running {workflow.title()} workflow...", None, None)
        logging.info(f"RUNNING {workflow.upper()} WORKFLOW")

        from autolamella import waffle as wfl # avoiding circular import
        if workflow == "trench":
            wfl.run_trench_milling(microscope, settings, experiment, parent_ui=self )

        if workflow == "undercut":
            wfl.run_undercut_milling(microscope, settings, experiment, parent_ui=self )

        if workflow == "setup-lamella":
            wfl.run_setup_lamella(microscope, settings, experiment, parent_ui=self )

        if workflow == "lamella":
            wfl.run_lamella_milling(microscope, settings, experiment, parent_ui=self )

        self.update_experiment_signal.emit(self.experiment)



def main():
    import autolamella
    autolamella_ui = AutoLamellaUI(viewer=napari.Viewer())
    autolamella_ui.viewer.window.add_dock_widget(
        autolamella_ui, area="right", 
        add_vertical_stretch=True, name=f"AutoLamella v{autolamella.__version__}"
    )
    napari.run()


if __name__ == "__main__":
    main()
