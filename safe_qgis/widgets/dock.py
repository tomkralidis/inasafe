# coding=utf-8
"""
InaSAFE Disaster risk assessment tool developed by AusAid - **GUI Dialog.**

Contact : ole.moller.nielsen@gmail.com

.. note:: This program is free software; you can redistribute it and/or modify
     it under the terms of the GNU General Public License as published by
     the Free Software Foundation; either version 2 of the License, or
     (at your option) any later version.

.. todo:: Check raster is single band

"""
__author__ = 'tim@linfiniti.com'
__revision__ = '$Format:%H$'
__date__ = '10/01/2011'
__copyright__ = ('Copyright 2012, Australia Indonesia Facility for '
                 'Disaster Reduction')

import os
import logging
from ConfigParser import ConfigParser
from functools import partial

import numpy
from PyQt4 import QtGui, QtCore
from PyQt4.QtGui import QFileDialog
from PyQt4.QtCore import pyqtSlot, QSettings, pyqtSignal
from qgis.core import (
    QgsMapLayer,
    QgsRasterLayer,
    QgsMapLayerRegistry,
    QgsCoordinateReferenceSystem,
    QGis)
from third_party.pydispatch import dispatcher
from safe_qgis.ui.dock_base import Ui_DockBase
from safe_qgis.utilities.help import Help
from safe_qgis.utilities.utilities import (
    get_error_message,
    getWGS84resolution,
    qgisVersion,
    impactLayerAttribution,
    addComboItemInOrder,
    extentToGeoArray,
    viewportGeoArray,
    readImpactLayer)
from safe_qgis.utilities.styling import (
    setRasterStyle,
    set_vector_graduated_style,
    set_vector_categorized_style)
from safe_qgis.utilities.memory_checker import check_memory_usage
from safe_qgis.utilities.impact_calculator import ImpactCalculator
from safe_qgis.safe_interface import (
    load_plugins,
    availableFunctions,
    get_function_title,
    getOptimalExtent,
    getBufferedExtent,
    getSafeImpactFunctions,
    safeTr,
    get_version,
    temp_dir,
    ReadLayerError,
    get_postprocessors,
    get_postprocessor_human_name,
    ZeroImpactException)
from safe_qgis.safe_interface import messaging as m
from safe_qgis.safe_interface import (
    DYNAMIC_MESSAGE_SIGNAL,
    STATIC_MESSAGE_SIGNAL,
    ERROR_MESSAGE_SIGNAL)
from safe_qgis.utilities.keyword_io import KeywordIO
from safe_qgis.utilities.clipper import clipLayer
from safe_qgis.impact_statistics.aggregator import Aggregator
from safe_qgis.impact_statistics.postprocessor_manager import (
    PostprocessorManager)
from safe_qgis.exceptions import (
    KeywordNotFoundError,
    KeywordDbError,
    InsufficientOverlapError,
    InvalidParameterError,
    InsufficientParametersError,
    HashNotFoundError,
    CallGDALError,
    NoFeaturesInExtentError,
    InvalidProjectionError,
    AggregatioError)
from safe_qgis.report.map import Map
from safe_qgis.report.html_renderer import HtmlRenderer
from safe_qgis.impact_statistics.function_options_dialog import (
    FunctionOptionsDialog)
from safe_qgis.tools.keywords_dialog import KeywordsDialog
from safe_qgis.safe_interface import styles

PROGRESS_UPDATE_STYLE = styles.PROGRESS_UPDATE_STYLE
INFO_STYLE = styles.INFO_STYLE
WARNING_STYLE = styles.WARNING_STYLE
KEYWORD_STYLE = styles.KEYWORD_STYLE
SUGGESTION_STYLE = styles.SUGGESTION_STYLE
LOGO_ELEMENT = m.Image('qrc:/plugins/inasafe/inasafe-logo.svg', 'InaSAFE Logo')
LOGGER = logging.getLogger('InaSAFE')

# from pydev import pydevd  # pylint: disable=F0401


#noinspection PyArgumentList
# noinspection PyUnresolvedReferences
class Dock(QtGui.QDockWidget, Ui_DockBase):
    """Dock implementation class for the inaSAFE plugin."""

    analysisDone = pyqtSignal(bool)

    def __init__(self, iface):
        """Constructor for the dialog.

        This dialog will allow the user to select layers and scenario details
        and subsequently run their model.

        :param iface: A QGisAppInterface instance we use to access QGIS via.
        :type iface: QgsAppInterface

        .. note:: We use the multiple inheritance approach from Qt4 so that
            for elements are directly accessible in the form context and we can
            use autoconnect to set up slots. See article below:
            http://doc.qt.nokia.com/4.7-snapshot/designer-using-a-ui-file.html
        """
        # Enable remote debugging - should normally be commented out.
        # pydevd.settrace(stdoutToServer=True, stderrToServer=True)

        QtGui.QDockWidget.__init__(self, None)
        self.setupUi(self)

        # Ensure that all impact functions are loaded
        load_plugins()
        self.pbnShowQuestion.setVisible(False)
        # Set up dispatcher for dynamic messages
        # Dynamic messages will not clear the message queue so will be appended
        # to existing user messages
        # noinspection PyArgumentEqualDefault
        dispatcher.connect(
            self.wvResults.dynamic_message_event,
            signal=DYNAMIC_MESSAGE_SIGNAL,
            sender=dispatcher.Any)
        # Set up dispatcher for static messages
        # Static messages clear the message queue and so the display is 'reset'
        # noinspection PyArgumentEqualDefault
        dispatcher.connect(
            self.wvResults.static_message_event,
            signal=STATIC_MESSAGE_SIGNAL,
            sender=dispatcher.Any)
        # Set up dispatcher for error messages
        # Static messages clear the message queue and so the display is 'reset'
        # noinspection PyArgumentEqualDefault
        dispatcher.connect(
            self.wvResults.error_message_event,
            signal=ERROR_MESSAGE_SIGNAL,
            sender=dispatcher.Any)

        myLongVersion = get_version()
        LOGGER.debug('Version: %s' % myLongVersion)
        myTokens = myLongVersion.split('.')
        myVersion = '%s.%s.%s' % (myTokens[0], myTokens[1], myTokens[2])
        try:
            myVersionType = myTokens[3].split('2')[0]
        except IndexError:
            myVersionType = 'final'
        # Allowed version names: ('alpha', 'beta', 'rc', 'final')
        self.setWindowTitle(self.tr('InaSAFE %1 %2').arg(
            myVersion, myVersionType))
        # Save reference to the QGIS interface
        self.iface = iface
        self.header = None  # for storing html header template
        self.footer = None  # for storing html footer template
        self.calculator = ImpactCalculator()
        self.keywordIO = KeywordIO()
        self.runner = None
        self.helpDialog = None
        self.state = None
        self.lastUsedFunction = ''
        self.runInThreadFlag = False
        self.showOnlyVisibleLayersFlag = True
        self.setLayerNameFromTitleFlag = True
        self.zoomToImpactFlag = True
        self.hideExposureFlag = True
        self.hazardLayers = None  # array of all hazard layers
        self.exposureLayers = None  # array of all exposure layers
        self._update_settings()  # fix old names in settings
        self.read_settings()  # getLayers called by this
        self.set_ok_button_status()
        self.aggregator = None
        self.postprocessorManager = None
        self.pbnPrint.setEnabled(False)
        # used by configurable function options button
        self.activeFunction = None
        self.runtimeKeywordsDialog = None

        myButton = self.pbnHelp
        QtCore.QObject.connect(
            myButton, QtCore.SIGNAL('clicked()'), self.show_help)

        myButton = self.pbnPrint
        QtCore.QObject.connect(
            myButton, QtCore.SIGNAL('clicked()'), self.print_map)
        #self.showHelp()
        myButton = self.pbnRunStop
        QtCore.QObject.connect(
            myButton, QtCore.SIGNAL('clicked()'), self.accept)
        #myAttribute = QtWebKit.QWebSettings.DeveloperExtrasEnabled
        #QtWebKit.QWebSettings.setAttribute(myAttribute, True)

        myCanvas = self.iface.mapCanvas()

        # Enable on the fly projection by default
        myCanvas.mapRenderer().setProjectionsEnabled(True)

    def show_static_message(self, theMessage):
        """Send a static message to the message viewer.

        Static messages cause any previous content in the MessageViewer to be
        replaced with new content.

        :param theMessage: Message - an instance of our rich message class.

        """
        dispatcher.send(
            signal=STATIC_MESSAGE_SIGNAL,
            sender=self,
            message=theMessage)

    def show_dynamic_message(self, theMessage):
        """Send a dynamic message to the message viewer.

        Dynamic messages are appended to any existing content in the
        MessageViewer.

        :param theMessage: An instance of our rich message class.
        :type theMessage: Message

        """
        dispatcher.send(
            signal=DYNAMIC_MESSAGE_SIGNAL,
            sender=self,
            message=theMessage)

    def show_error_message(self, theErrorMessage):
        """Send an error message to the message viewer.

        Error messages cause any previous content in the MessageViewer to be
        replaced with new content.

        :param theErrorMessage: An instance of our rich error message class.
        :type theErrorMessage: ErrorMessage
        """
        dispatcher.send(
            signal=ERROR_MESSAGE_SIGNAL,
            sender=self,
            message=theErrorMessage)
        self.hide_busy()

    def read_settings(self):
        """Set the dock state from QSettings.

        Do this on init and after changing options in the options dialog.
        """

        mySettings = QtCore.QSettings()
        myFlag = mySettings.value('inasafe/useThreadingFlag',
                                  False).toBool()
        self.runInThreadFlag = myFlag

        myFlag = mySettings.value(
            'inasafe/visibleLayersOnlyFlag', True).toBool()
        self.showOnlyVisibleLayersFlag = myFlag

        myFlag = mySettings.value(
            'inasafe/setLayerNameFromTitleFlag', True).toBool()
        self.setLayerNameFromTitleFlag = myFlag

        myFlag = mySettings.value(
            'inasafe/setZoomToImpactFlag', True).toBool()
        self.zoomToImpactFlag = myFlag
        # whether exposure layer should be hidden after model completes
        myFlag = mySettings.value(
            'inasafe/setHideExposureFlag', False).toBool()
        self.hideExposureFlag = myFlag

        # whether to clip hazard and exposure layers to the viewport
        myFlag = mySettings.value(
            'inasafe/clipToViewport', True).toBool()
        self.clipToViewport = myFlag

        # whether to 'hard clip' layers (e.g. cut buildings in half if they
        # lie partially in the AOI
        myFlag = mySettings.value(
            'inasafe/clipHard', False).toBool()
        self.clipHard = myFlag

        # whether to show or not postprocessing generated layers
        myFlag = mySettings.value(
            'inasafe/showIntermediateLayers', False).toBool()
        self.showIntermediateLayers = myFlag

        # whether to show or not dev only options
        myFlag = mySettings.value(
            'inasafe/devMode', False).toBool()
        self.devMode = myFlag

        self.get_layers()

    def _update_settings(self):
        """Update setting to new settings names."""

        mySettings = QtCore.QSettings()
        myOldFlag = mySettings.value(
            'inasafe/showPostProcLayers', False).toBool()
        mySettings.remove('inasafe/showPostProcLayers')

        if not mySettings.contains('inasafe/showIntermediateLayers'):
            mySettings.setValue('inasafe/showIntermediateLayers', myOldFlag)

    def connect_layer_listener(self):
        """Establish a signal/slot to listen for layers loaded in QGIS.

        ..seealso:: disconnectLayerListener
        """
        if qgisVersion() >= 10800:  # 1.8 or newer
            QgsMapLayerRegistry.instance().layersWillBeRemoved.connect(
                self.layers_will_be_removed)
            QgsMapLayerRegistry.instance().layersAdded.connect(
                self.layers_added)
        # All versions of QGIS
        QtCore.QObject.connect(
            self.iface.mapCanvas(),
            QtCore.SIGNAL('layersChanged()'),
            self.get_layers)
        QtCore.QObject.connect(
            self.iface,
            QtCore.SIGNAL("currentLayerChanged(QgsMapLayer*)"),
            self.layer_changed)

    # pylint: disable=W0702
    def disconnect_layer_listener(self):
        """Destroy the signal/slot to listen for layers loaded in QGIS.

        ..seealso:: connectLayerListener
        """
        # noinspection PyBroadException
        try:
            QtCore.QObject.disconnect(
                QgsMapLayerRegistry.instance(),
                QtCore.SIGNAL('layerWillBeRemoved(QString)'),
                self.get_layers)
        except:
            pass

        # noinspection PyBroadException
        try:
            QtCore.QObject.disconnect(
                QgsMapLayerRegistry.instance(),
                QtCore.SIGNAL('layerWasAdded(QgsMapLayer)'),
                self.get_layers)
        except:
            pass

        # noinspection PyBroadException
        try:
            QgsMapLayerRegistry.instance().layers_will_be_removed.disconnect(
                self.layers_will_be_removed)
            QgsMapLayerRegistry.instance().layers_added.disconnect(
                self.layers_added)
        except:
            pass

        # noinspection PyBroadException
        try:
            QtCore.QObject.disconnect(
                self.iface.mapCanvas(),
                QtCore.SIGNAL('layersChanged()'),
                self.get_layers)
        except:
            pass

        QtCore.QObject.disconnect(
            self.iface,
            QtCore.SIGNAL("currentLayerChanged(QgsMapLayer*)"),
            self.layer_changed)
    # pylint: enable=W0702

    def getting_started_message(self):
        """Generate a message for initial application state.

        :returns: Information for the user on how to get started.
        :rtype: Message
        """
        myMessage = m.Message()
        myMessage.add(LOGO_ELEMENT)
        myMessage.add(m.Heading('Getting started -', **INFO_STYLE))
        myNotes = m.Paragraph(
            self.tr(
                'To use this tool you need to add some layers to your '
                'QGIS project. Ensure that at least one'),
            m.EmphasizedText(self.tr('hazard'), **KEYWORD_STYLE),
            self.tr('layer (e.g. earthquake MMI) and one '),
            m.EmphasizedText(self.tr('exposure'), **KEYWORD_STYLE),
            self.tr(
                'layer (e.g. structures) are available. When you are '
                'ready, click the '),
            m.EmphasizedText(self.tr('run'), **KEYWORD_STYLE),
            self.tr('button below.'))
        myMessage.add(myNotes)
        myMessage.add(m.Heading('Limitations', **WARNING_STYLE))
        myList = m.NumberedList()
        myList.add(
            self.tr('InaSAFE is not a hazard modelling tool.'))
        myList.add(
            self.tr(
                'Exposure data in the form of roads (or any other line '
                'feature) is not yet supported.'))
        myList.add(
            self.tr(
                'Polygon area analysis (such as land use) is not yet '
                'supported.'))
        myList.add(
            self.tr(
                'Population density data must be provided in WGS84 '
                'geographic coordinates.'))
        myList.add(
            self.tr(
                'Neither BNPB, AusAID, nor the World Bank-GFDRR, take any '
                'responsibility for the correctness of outputs from InaSAFE '
                'or decisions derived as a consequence.'))
        myMessage.add(myList)
        return myMessage

    def ready_message(self):
        """Helper to create a message indicating inasafe is ready.

        :returns Message: A localised message indicating we are ready to run.
        """
        # What does this todo mean? TS
        # TODO refactor impact_functions so it is accessible and user here
        myTitle = m.Heading(
            self.tr('Ready'), **PROGRESS_UPDATE_STYLE)
        myNotes = m.Paragraph(self.tr(
            'You can now proceed to run your model by clicking the'),
            m.EmphasizedText(self.tr('run'), **KEYWORD_STYLE),
            self.tr('button.'))
        myMessage = m.Message(LOGO_ELEMENT, myTitle, myNotes)
        return myMessage

    def not_ready_message(self):
        """Help to create a message indicating inasafe is NOT ready.

        :returns Message: A localised message indicating we are not ready.
        """
        # What does this todo mean? TS
        # TODO refactor impact_functions so it is accessible and user here
        #myHazardFilename = self.getHazardLayer().source()
        myHazardKeywords = QtCore.QString(str(
            self.keywordIO.read_keywords(self.get_hazard_layer())))
        #myExposureFilename = self.getExposureLayer().source()
        myExposureKeywords = QtCore.QString(
            str(self.keywordIO.read_keywords(self.get_exposure_layer())))
        myHeading = m.Heading(
            self.tr('No valid functions:'), **WARNING_STYLE)
        myNotes = m.Paragraph(self.tr(
            'No functions are available for the inputs you have specified. '
            'Try selecting a different combination of inputs. Please '
            'consult the user manual for details on what constitute '
            'valid inputs for a given risk function.'))
        myHazardHeading = m.Heading(
            self.tr('Hazard keywords:'), **INFO_STYLE)
        myHazardKeywords = m.Paragraph(myHazardKeywords)
        myExposureHeading = m.Heading(
            self.tr('Exposure keywords:'), **INFO_STYLE)
        myExposureKeywords = m.Paragraph(myExposureKeywords)
        myMessage = m.Message(
            myHeading,
            myNotes,
            myExposureHeading,
            myExposureKeywords,
            myHazardHeading,
            myHazardKeywords)
        return myMessage

    def validate(self):
        """Helper method to evaluate the current state of the dialog.

        This function will determine if it is appropriate for the OK button to
        be enabled or not.

        .. note:: The enabled state of the OK button on the dialog will
           NOT be updated (set True or False) depending on the outcome of
           the UI readiness tests performed - **only** True or False
           will be returned by the function.

        :returns: A two-tuple where the first element is a Boolean reflecting
         the results of the validation tests and the second is a message
         indicating any reason why the validation may have failed.
        :rtype: (Boolean, Message)

        Example::

            flag,myMessage = self.validate()
        """
        myHazardIndex = self.cboHazard.currentIndex()
        myExposureIndex = self.cboExposure.currentIndex()
        if myHazardIndex == -1 or myExposureIndex == -1:
            myMessage = self.getting_started_message()
            return False, myMessage

        if self.cboFunction.currentIndex() == -1:
            myMessage = self.not_ready_message()
            return False, myMessage
        else:
            myMessage = self.ready_message()
            return True, myMessage

    def on_cboHazard_currentIndexChanged(self, theIndex):
        """Automatic slot executed when the Hazard combo is changed.

        This is here so that we can see if the ok button should be enabled.

        :param theIndex: The index number of the selected hazard layer.

        .. note:: Don't use the @pyqtSlot() decorator for autoslots!
        """
        # Add any other logic you might like here...
        del theIndex
        self.get_functions()
        self.toggle_aggregation_combo()
        self.set_ok_button_status()

    def on_cboExposure_currentIndexChanged(self, theIndex):
        """Automatic slot executed when the Exposure combo is changed.

        This is here so that we can see if the ok button should be enabled.

        :param theIndex: The index number of the selected exposure layer.

        .. note:: Don't use the @pyqtSlot() decorator for autoslots!
        """
        # Add any other logic you might like here...
        del theIndex
        self.get_functions()
        self.toggle_aggregation_combo()
        self.set_ok_button_status()

    @pyqtSlot(QtCore.QString)
    def on_cboFunction_currentIndexChanged(self, theIndex):
        """Automatic slot executed when the Function combo is changed.

        This is here so that we can see if the ok button should be enabled.

        :param theIndex: The index number of the selected function.
        """
        # Add any other logic you might like here...
        if not theIndex.isNull or not theIndex == '':
            myFunctionID = self.get_function_id()

            myFunctions = getSafeImpactFunctions(myFunctionID)
            self.activeFunction = myFunctions[0][myFunctionID]
            self.functionParams = None
            if hasattr(self.activeFunction, 'parameters'):
                self.functionParams = self.activeFunction.parameters
            self.set_function_options_status()

        self.toggle_aggregation_combo()
        self.set_ok_button_status()

    def toggle_aggregation_combo(self):
        """Toggle the aggregation combo enabled status.

        Whether the combo is toggled on or off will depend on the current dock
        status.
        """
        selectedHazardLayer = self.get_hazard_layer()
        selectedExposureLayer = self.get_exposure_layer()

        #more than 1 because No aggregation is always there
        if ((self.cboAggregation.count() > 1) and
                (selectedHazardLayer is not None) and
                (selectedExposureLayer is not None)):
            self.cboAggregation.setEnabled(True)
        else:
            self.cboAggregation.setCurrentIndex(0)
            self.cboAggregation.setEnabled(False)

    def set_ok_button_status(self):
        """Helper function to set the ok button status based on form validity.
        """
        myButton = self.pbnRunStop
        myFlag, myMessage = self.validate()
        myButton.setEnabled(myFlag)
        if myMessage is not '':
            self.show_static_message(myMessage)

    def set_function_options_status(self):
        """Helper function to toggle the tool function button based on context.

        If there are function parameters to configure then enable it, otherwise
        disable it.
        """
        # Check if functionParams intialized
        if self.functionParams is None:
            self.toolFunctionOptions.setEnabled(False)
        else:
            self.toolFunctionOptions.setEnabled(True)

    @pyqtSlot()
    def on_toolFunctionOptions_clicked(self):
        """Automatic slot executed when toolFunctionOptions is clicked."""
        myDialog = FunctionOptionsDialog(self)
        myDialog.setDialogInfo(self.get_function_id())
        myDialog.buildForm(self.functionParams)

        if myDialog.exec_():
            self.activeFunction.parameters = myDialog.result()
            self.functionParams = self.activeFunction.parameters

    def canvas_layerset_changed(self):
        """A helper slot to update dock combos if canvas layerset changes.

        Activated when the layerset has been changed (e.g. one or more layer
        visibilities changed). If self.showOnlyVisibleLayersFlag is set to
        False this method will simply return, doing nothing.
        """
        if self.showOnlyVisibleLayersFlag:
            self.get_layers()

    @pyqtSlot()
    def layers_will_be_removed(self):
        """QGIS 1.8+ slot to notify us when a group of layers are removed.

        This is optimal since if many layers are removed this slot gets called
        only once. This slot simply delegates to getLayers and is only
        implemented here to make the connections between the different signals
        and slots clearer and better documented.

        .. note:: Requires QGIS 1.8 and better api.

        """
        self.get_layers()

    @pyqtSlot()
    def layers_added(self, theLayers=None):
        """QGIS 1.8+ slot to notify us when a group of layers are added.

        Slot for the new (QGIS 1.8 and beyond api) to notify us when
        a group of layers is are added. This is optimal since if many layers
        are added this slot gets called only once. This slot simply
        delegates to getLayers and is only implemented here to make the
        connections between the different signals and slots clearer and
        better documented.

        :param theLayers: This paramters is ignored but required for the slot
         signature.

        .. note:: Requires QGIS 1.8 and better api.

        """
        del theLayers
        self.get_layers()

    @pyqtSlot()
    def layer_will_be_removed(self):
        """Slot for the old (pre QGIS 1.8 api) notifying a layer was removed.

        This is suboptimal since if many layers are removed this slot gets
        called multiple times. This slot simply delegates to getLayers and is
        only implemented here to make the connections between the different
        signals and slots clearer and better documented."""

        self.get_layers()

    @pyqtSlot()
    def layer_was_added(self):
        """QGIS <= 1.7.x slot to notify us when a layer was added.

        Slot for the old (pre QGIS 1.8 api) to notify us when
        a layer is added. This is suboptimal since if many layers are
        added this slot gets called multiple times. This slot simply
        delegates to getLayers and is only implemented here to make the
        connections between the different signals and slots clearer and
        better documented.

        ..note :: see :func:`layersAdded` - this slot will be deprecated
            eventually.

        """
        self.get_layers()

    def get_layers(self):
        """Helper function to obtain a list of layers currently loaded in QGIS.

        On invocation, this method will populate cboHazard,
        cboExposure and cboAggregate on the dialog with a list of available
        layers.
        Only **singleband raster** layers will be added to the hazard layer
        list,only **point vector** layers will be added to the exposure layer
        list and Only **polygon vector** layers will be added to the aggregate
        list

        Args:
           None.
        Returns:
           None
        Raises:
           no
        """
        self.disconnect_layer_listener()
        self.blockSignals(True)
        self.save_state()
        self.cboHazard.clear()
        self.cboExposure.clear()
        self.cboAggregation.clear()
        self.hazardLayers = []
        self.exposureLayers = []
        self.aggregationLayers = []
        # Map registry may be invalid if QGIS is shutting down
        # pylint: disable=W0702
        # noinspection PyBroadException
        try:
            myRegistry = QgsMapLayerRegistry.instance()
        except:
            return
        # pylint: enable=W0702

        myCanvasLayers = self.iface.mapCanvas().layers()

        # MapLayers returns a QMap<QString id, QgsMapLayer layer>
        myLayers = myRegistry.mapLayers().values()
        for myLayer in myLayers:
            if (self.showOnlyVisibleLayersFlag and
                    (myLayer not in myCanvasLayers)):
                continue

            # .. todo:: check raster is single band
            #    store uuid in user property of list widget for layers

            myName = myLayer.name()
            mySource = str(myLayer.id())
            # See if there is a title for this layer, if not,
            # fallback to the layer's filename

            # noinspection PyBroadException
            try:
                myTitle = self.keywordIO.read_keywords(myLayer, 'title')
            except:  # pylint: disable=W0702
                # automatically adding file name to title in keywords
                # See #575
                self.keywordIO.update_keywords(myLayer, {'title': myName})
                myTitle = myName
            else:
                # Lookup internationalised title if available
                myTitle = safeTr(myTitle)
            # Register title with layer
            if myTitle and self.setLayerNameFromTitleFlag:
                myLayer.setLayerName(myTitle)

            # NOTE : I commented out this due to
            # https://github.com/AIFDR/inasafe/issues/528
            # check if layer is a vector polygon layer
            # if isPolygonLayer(myLayer):
            #     addComboItemInOrder(self.cboAggregation, myTitle,
            #                         mySource)
            #     self.aggregationLayers.append(myLayer)

            # Find out if the layer is a hazard or an exposure
            # layer by querying its keywords. If the query fails,
            # the layer will be ignored.
            # noinspection PyBroadException
            try:
                myCategory = self.keywordIO.read_keywords(myLayer, 'category')
            except:  # pylint: disable=W0702
                # continue ignoring this layer
                continue

            if myCategory == 'hazard':
                addComboItemInOrder(self.cboHazard, myTitle, mySource)
                self.hazardLayers.append(myLayer)
            elif myCategory == 'exposure':
                addComboItemInOrder(self.cboExposure, myTitle, mySource)
                self.exposureLayers.append(myLayer)
            elif myCategory == 'postprocessing':
                addComboItemInOrder(self.cboAggregation, myTitle, mySource)
                self.aggregationLayers.append(myLayer)

        #handle the cboAggregation combo
        self.cboAggregation.insertItem(0, self.tr('Entire area'))
        self.cboAggregation.setCurrentIndex(0)
        self.toggle_aggregation_combo()

        # Now populate the functions list based on the layers loaded
        self.get_functions()
        self.restore_state()
        self.set_ok_button_status()
        # Note: Don't change the order of the next two lines otherwise there
        # will be a lot of unneeded looping around as the signal is handled
        self.connect_layer_listener()
        self.blockSignals(False)
        return

    def get_functions(self):
        """Obtain a list of impact functions from the impact calculator.
        """
        # remember what the current function is
        myOriginalFunction = self.cboFunction.currentText()
        self.cboFunction.clear()

        # Get the keyword dictionaries for hazard and exposure
        myHazardLayer = self.get_hazard_layer()
        if myHazardLayer is None:
            return
        myExposureLayer = self.get_exposure_layer()
        if myExposureLayer is None:
            return
        myHazardKeywords = self.keywordIO.read_keywords(myHazardLayer)
        # We need to add the layer type to the returned keywords
        if myHazardLayer.type() == QgsMapLayer.VectorLayer:
            myHazardKeywords['layertype'] = 'vector'
        elif myHazardLayer.type() == QgsMapLayer.RasterLayer:
            myHazardKeywords['layertype'] = 'raster'

        myExposureKeywords = self.keywordIO.read_keywords(myExposureLayer)
        # We need to add the layer type to the returned keywords
        if myExposureLayer.type() == QgsMapLayer.VectorLayer:
            myExposureKeywords['layertype'] = 'vector'
        elif myExposureLayer.type() == QgsMapLayer.RasterLayer:
            myExposureKeywords['layertype'] = 'raster'

        # Find out which functions can be used with these layers
        myList = [myHazardKeywords, myExposureKeywords]
        try:
            myDict = availableFunctions(myList)
            # Populate the hazard combo with the available functions
            for myFunctionID in myDict:
                myFunction = myDict[myFunctionID]
                myFunctionTitle = get_function_title(myFunction)

                # KEEPING THESE STATEMENTS FOR DEBUGGING UNTIL SETTLED
                #print
                #print 'myFunction (ID)', myFunctionID
                #print 'myFunction', myFunction
                #print 'Function title:', myFunctionTitle

                # Provide function title and ID to function combo:
                # myFunctionTitle is the text displayed in the combo
                # myFunctionID is the canonical identifier
                addComboItemInOrder(
                    self.cboFunction,
                    myFunctionTitle,
                    theItemData=myFunctionID)
        except Exception, e:
            raise e

        self.restore_function_state(myOriginalFunction)

    def get_hazard_layer(self):
        """Get the QgsMapLayer currently selected in the hazard combo.

        Obtain QgsMapLayer id from the userrole of the QtCombo for hazard
        and return it as a QgsMapLayer.

        :returns: The currently selected map layer in the hazard combo.
        :rtype: QgsMapLayer

        """
        myIndex = self.cboHazard.currentIndex()
        if myIndex < 0:
            return None
        myLayerId = self.cboHazard.itemData(
            myIndex, QtCore.Qt.UserRole).toString()
        myLayer = QgsMapLayerRegistry.instance().mapLayer(myLayerId)
        return myLayer

    def get_exposure_layer(self):
        """Get the QgsMapLayer currently selected in the exposure combo.

        Obtain QgsMapLayer id from the userrole of the QtCombo for exposure
        and return it as a QgsMapLayer.

        Args:
            None

        Returns:
            QgsMapLayer - currently selected map layer in the exposure combo.

        Raises:
            None
        """

        myIndex = self.cboExposure.currentIndex()
        if myIndex < 0:
            return None
        myLayerId = self.cboExposure.itemData(
            myIndex, QtCore.Qt.UserRole).toString()
        myLayer = QgsMapLayerRegistry.instance().mapLayer(myLayerId)
        return myLayer

    def get_aggregation_layer(self):

        """Get the QgsMapLayer currently selected in the post processing combo.

        Obtain QgsMapLayer id from the userrole of the QtCombo for post
        processing combo return it as a QgsMapLayer.

        :returns: None if no aggregation is selected or cboAggregation is
                disabled, otherwise a polygon layer.
        :rtype: QgsMapLayer or None
        """

        myNoSelectionValue = 0
        myIndex = self.cboAggregation.currentIndex()
        if myIndex <= myNoSelectionValue:
            return None
        myLayerId = self.cboAggregation.itemData(
            myIndex, QtCore.Qt.UserRole).toString()
        myLayer = QgsMapLayerRegistry.instance().mapLayer(myLayerId)
        return myLayer

    def setup_calculator(self):
        """Initialise ImpactCalculator based on the current state of the ui.

        Args:
            None

        Returns:
            None

        Raises:
            Propagates any error from :func:optimalClip()
        """

        myHazardLayer, myExposureLayer = self.optimal_clip()
        # See if the inputs need further refinement for aggregations
        self.aggregator.deintersect(myHazardLayer, myExposureLayer)
        # Identify input layers
        self.calculator.setHazardLayer(self.aggregator.hazardLayer.source())
        self.calculator.setExposureLayer(
            self.aggregator.exposureLayer.source())

        # Use canonical function name to identify selected function
        myFunctionID = self.get_function_id()
        self.calculator.setFunction(myFunctionID)

    def prepare_aggregator(self):
        """Create an aggregator for this analysis run."""
        self.aggregator = Aggregator(
            self.iface,
            self.get_aggregation_layer())
        self.aggregator.showIntermediateLayers = self.showIntermediateLayers
        # Buffer aggregation keywords in case user presses cancel on kw dialog
        try:
            myOriginalKeywords = self.keywordIO.read_keywords(
                self.aggregator.layer)
        except AttributeError:
            myOriginalKeywords = {}
        except InvalidParameterError:
            #No kw file was found for layer - create an empty one.
            myOriginalKeywords = {}
            self.keywordIO.write_keywords(
                self.aggregator.layer, myOriginalKeywords)
        LOGGER.debug('my pre dialog keywords' + str(myOriginalKeywords))
        LOGGER.debug(
            'AOImode: %s' % str(self.aggregator.aoiMode))
        self.runtimeKeywordsDialog = KeywordsDialog(
            self.iface.mainWindow(),
            self.iface,
            self,
            self.aggregator.layer)
        QtCore.QObject.connect(
            self.runtimeKeywordsDialog,
            QtCore.SIGNAL('accepted()'),
            self.run)
        QtCore.QObject.connect(
            self.runtimeKeywordsDialog,
            QtCore.SIGNAL('rejected()'),
            partial(self.accept_cancelled, myOriginalKeywords))

    def accept(self):
        """Execute analysis when run button is clicked.

        .. todo:: FIXME (Tim) We may have to implement some polling logic
            because the button click accept() function and the updating
            of the web view after model completion are asynchronous (when
            threading mode is enabled especially)
        """

        myTitle = self.tr('Processing started')
        myDetails = self.tr(
            'Please wait - processing may take a while depending on your '
            'hardware configuration and the analysis extents and data.')
        #TODO style these.
        myText = m.Text(
            self.tr('This analysis will calculate the impact of'),
            m.EmphasizedText(self.get_hazard_layer().name()),
            self.tr('on'),
            m.EmphasizedText(self.get_exposure_layer().name()),
        )

        if self.get_aggregation_layer() is not None:
            myText.add(m.Text(
                self.tr('and list the results'),
                m.ImportantText(self.tr('aggregated by')),
                m.EmphasizedText(self.get_aggregation_layer().name()))
            )
        myText.add('.')

        myMessage = m.Message(
            LOGO_ELEMENT,
            m.Heading(myTitle, **PROGRESS_UPDATE_STYLE),
            m.Paragraph(myDetails),
            m.Paragraph(myText))

        try:
            #add which postprocessors will run when appropriated
            myRequestedPostProcessors = self.functionParams['postprocessors']
            myPostProcessors = get_postprocessors(myRequestedPostProcessors)
            myMessage.add(m.Paragraph(self.tr(
                'The following postprocessors will be used:')))

            myList = m.BulletedList()

            for myName, myPostProcessor in myPostProcessors.iteritems():
                myList.add('%s: %s' % (
                    get_postprocessor_human_name(myName),
                    myPostProcessor.description()))
            myMessage.add(myList)

        except (TypeError, KeyError):
            # TypeError is for when functionParams is none
            # KeyError is for when ['postprocessors'] is unavailable
            pass

        self.show_static_message(myMessage)

        try:
            # See if we are re-running the same type of analysis, if not
            # we should prompt the user for new keywords for agg layer.
            self.check_for_state_change()
        except (KeywordDbError, Exception), e:   # pylint: disable=W0703
            myContext = self.tr(
                'A problem was encountered when trying to read keywords.'
            )
            self.analysis_error(e, myContext)
            return

        # Find out what the usable extent and cellsize are
        try:
            _, myBufferedGeoExtent, myCellSize, _, _, _ = \
                self.get_clip_parameters()
        except (RuntimeError, InsufficientOverlapError, AttributeError) as e:
            LOGGER.exception('Error calculating extents. %s' % str(e.message))
            myContext = self.tr(
                'A problem was encountered when trying to determine the '
                'analysis extents.'
            )
            self.analysis_error(e, myContext)
            return  # ignore any error

        # Ensure there is enough memory
        myResult = check_memory_usage(myBufferedGeoExtent, myCellSize)
        if not myResult:
            # noinspection PyCallByClass,PyTypeChecker
            myResult = QtGui.QMessageBox.warning(
                self, self.tr('InaSAFE'),
                self.tr('You may not have sufficient free system memory to '
                        'carry out this analysis. See the dock panel '
                        'message for more information. Would you like to '
                        'continue regardless?'), QtGui.QMessageBox.Yes |
                QtGui.QMessageBox.No, QtGui.QMessageBox.No)
            if myResult == QtGui.QMessageBox.No:
                # stop work here and return to QGIS
                self.hide_busy()
                return

        self.prepare_aggregator()

        # go check if our postprocessing layer has any keywords set and if not
        # prompt for them. if a prompt is shown run method is called by the
        # accepted signal of the keywords dialog
        self.aggregator.validateKeywords()
        if self.aggregator.aoiMode and self.aggregator.isValid:
            self.run()
        else:
            self.runtimeKeywordsDialog.setLayer(self.aggregator.layer)
            #disable gui elements that should not be applicable for this
            self.runtimeKeywordsDialog.radExposure.setEnabled(False)
            self.runtimeKeywordsDialog.radHazard.setEnabled(False)
            self.runtimeKeywordsDialog.pbnAdvanced.setEnabled(False)
            self.runtimeKeywordsDialog.setModal(True)
            self.runtimeKeywordsDialog.show()

    def accept_cancelled(self, theOldKeywords):
        """Deal with user cancelling post processing option dialog.

        :param theOldKeywords: A keywords dictionary that should be reinstated.
        :type theOldKeywords: dict
        """
        LOGGER.debug('Setting old dictionary: ' + str(theOldKeywords))
        self.keywordIO.write_keywords(self.aggregator.layer, theOldKeywords)
        self.hide_busy()
        self.set_ok_button_status()

    def check_for_state_change(self):
        """Clear aggregation layer category keyword on dock state change.
        """
        #check and generate keywords for the aggregation layer
        try:
            if ((self.get_aggregation_layer() is not None) and
                    (self.lastUsedFunction != self.get_function_id())):
                # Remove category keyword so we force the keyword editor to
                # popup. See the beginning of checkAttributes to
                # see how the popup decision is made
                self.keywordIO.delete_keywords(self.layer, 'category')
        except AttributeError:
            #first run, self.lastUsedFunction does not exist yet
            pass

    def show_busy(self):
        """Hide the question group box and enable the busy cursor."""
        self.grpQuestion.setEnabled(False)
        self.grpQuestion.setVisible(False)
        QtGui.qApp.setOverrideCursor(QtGui.QCursor(QtCore.Qt.WaitCursor))
        self.repaint()
        QtGui.qApp.processEvents()

    def run(self):
        """Execute analysis when ok button on dock is clicked."""

        self.enable_busy_cursor()

        # Start the analysis
        try:
            self.setup_calculator()
        except CallGDALError, e:
            self.analysis_error(e, self.tr(
                'An error occurred when calling a GDAL command'))
            return
        except IOError, e:
            self.analysis_error(e, self.tr(
                'An error occurred when writing clip file'))
            return
        except InsufficientOverlapError, e:
            self.analysis_error(e, self.tr(
                'An exception occurred when setting up the impact calculator.')
            )
            return
        except NoFeaturesInExtentError, e:
            self.analysis_error(e, self.tr(
                'An error occurred because there are no features visible in '
                'the current view. Try zooming out or panning until some '
                'features become visible.'))
            return
        except InvalidProjectionError, e:
            self.analysis_error(e, self.tr(
                'An error occurred because you are using a layer containing '
                'density data (e.g. population density) which will not '
                'scale accurately if we re-project it from its native '
                'coordinate reference system to WGS84/GeoGraphic.'))
            return
        except MemoryError, e:
            self.analysis_error(
                e,
                self.tr(
                    'An error occurred because it appears that your '
                    'system does not have sufficient memory. Upgrading '
                    'your computer so that it has more memory may help. '
                    'Alternatively, consider using a smaller geographical '
                    'area for your analysis, or using rasters with a larger '
                    'cell size.'))
            return

        try:
            self.runner = self.calculator.getRunner()
        except (InsufficientParametersError, ReadLayerError), e:
            self.analysis_error(
                e,
                self.tr(
                    'An exception occurred when setting up the model runner.'))
            return

        QtCore.QObject.connect(
            self.runner, QtCore.SIGNAL('done()'), self.aggregate)

        self.show_busy()

        myTitle = self.tr('Calculating impact')
        myDetail = self.tr(
            'This may take a little while - we are computing the areas that '
            'will be impacted by the hazard and writing the result to a new '
            'layer.')
        myMessage = m.Message(
            m.Heading(myTitle, **PROGRESS_UPDATE_STYLE),
            m.Paragraph(myDetail))
        self.show_dynamic_message(myMessage)
        try:
            if self.runInThreadFlag:
                self.runner.start()  # Run in different thread
            else:
                self.runner.run()  # Run in same thread
            QtGui.qApp.restoreOverrideCursor()
            # .. todo :: Disconnect done slot/signal

        except Exception, e:  # pylint: disable=W0703

            # FIXME (Ole): This branch is not covered by the tests
            self.analysis_error(
                e,
                self.tr('An exception occurred when starting the model.'))

    def analysis_error(self, theException, theMessage):
        """A helper to spawn an error and halt processing.

        An exception will be logged, busy status removed and a message
        displayed.

        :param theMessage: an ErrorMessage to display
        :type theMessage: ErrorMessage, Message

        :param theException: An exception that was raised
        :type theException: Exception
        """
        QtGui.qApp.restoreOverrideCursor()
        self.hide_busy()
        LOGGER.exception(theMessage)
        myMessage = get_error_message(theException, theContext=theMessage)
        self.show_error_message(myMessage)
        self.analysisDone.emit(False)

    def completed(self):
        """Slot activated when the process is done.
        """
        #save the ID of the function that just ran
        self.lastUsedFunction = self.get_function_id()

        # Try to run completion code
        try:
            myEngineImpactLayer = self.runner.impactLayer()

            # Load impact layer into QGIS
            myQGISImpactLayer = readImpactLayer(myEngineImpactLayer)
            self.layer_changed(myQGISImpactLayer)
            myReport = self.show_results(myQGISImpactLayer, myEngineImpactLayer)
        except Exception, e:  # pylint: disable=W0703

            # FIXME (Ole): This branch is not covered by the tests
            self.analysis_error(e, self.tr('Error loading impact layer.'))
        else:
            # On success, display generated report
            self.show_dynamic_message(m.Message(str(myReport)))
        self.save_state()
        self.hide_busy()
        self.analysisDone.emit(True)

    def show_results(self, theQGISImpactLayer, theEngineImpactLayer):
        """Helper function for slot activated when the process is done.

        :param theQGISImpactLayer: A QGIS layer representing the impact.
        :type theQGISImpactLayer: QgsMapLayer, QgsVectorLayer, QgsRasterLayer

        :param theEngineImpactLayer: A safe_layer representing the impact.
        :type theEngineImpactLayer: ReadLayer

        :returns: Provides a report for writing to the dock.
        :rtype: str
        """

        myTitle = self.tr('Loading results...')
        myDetail = self.tr(
            'The impact assessment is complete - loading the results into '
            'QGIS now...')
        myMessage = m.Message(m.Heading(myTitle, level=3), myDetail)
        self.show_dynamic_message(myMessage)

        myKeywords = self.keywordIO.read_keywords(theQGISImpactLayer)

        #write postprocessing report to keyword
        myOutput = self.postprocessorManager.getOutput()
        myKeywords['postprocessing_report'] = myOutput.to_html(
            suppress_newlines=True)
        self.keywordIO.write_keywords(theQGISImpactLayer, myKeywords)

        # Get tabular information from impact layer
        myReport = self.keywordIO.read_keywords(
            theQGISImpactLayer, 'impact_summary')
        myReport += impactLayerAttribution(myKeywords).to_html(True)

        # Get requested style for impact layer of either kind
        myStyle = theEngineImpactLayer.get_style_info()
        myStyleType = theEngineImpactLayer.get_style_type()

        # Determine styling for QGIS layer
        if theEngineImpactLayer.is_vector:
            LOGGER.debug('myEngineImpactLayer.is_vector')
            if not myStyle:
                # Set default style if possible
                pass
            elif myStyleType == 'categorizedSymbol':
                LOGGER.debug('use categorized')
                set_vector_categorized_style(theQGISImpactLayer, myStyle)
            elif myStyleType == 'graduatedSymbol':
                LOGGER.debug('use graduated')
                set_vector_graduated_style(theQGISImpactLayer, myStyle)

        elif theEngineImpactLayer.is_raster:
            LOGGER.debug('myEngineImpactLayer.is_raster')
            if not myStyle:
                theQGISImpactLayer.setDrawingStyle(
                    QgsRasterLayer.SingleBandPseudoColor)
                theQGISImpactLayer.setColorShadingAlgorithm(
                    QgsRasterLayer.PseudoColorShader)
            else:
                setRasterStyle(theQGISImpactLayer, myStyle)

        else:
            myMessage = self.tr('Impact layer %1 was neither a raster or a '
                                'vector layer').arg(
                                    theQGISImpactLayer.source())
            # noinspection PyExceptionInherit
            raise ReadLayerError(myMessage)

        # Add layers to QGIS
        myLayersToAdd = []
        if self.showIntermediateLayers:
            myLayersToAdd.append(self.aggregator.layer)
        myLayersToAdd.append(theQGISImpactLayer)
        QgsMapLayerRegistry.instance().addMapLayers(myLayersToAdd)
        # then zoom to it
        if self.zoomToImpactFlag:
            self.iface.zoomToActiveLayer()
        if self.hideExposureFlag:
            myExposureLayer = self.get_exposure_layer()
            myLegend = self.iface.legendInterface()
            myLegend.setLayerVisible(myExposureLayer, False)
        self.restore_state()

        #append postprocessing report
        myReport += myOutput.to_html()

        # Return text to display in report panel
        return myReport

    def show_help(self):
        """Load the help text into the system browser."""
        if self.helpDialog:
            del self.helpDialog
        self.helpDialog = Help(
            theParent=self.iface.mainWindow(), theContext='dock')

    def hide_busy(self):
        """A helper function to indicate processing is done."""
        #self.pbnRunStop.setText('Run')
        if self.runner:
            QtCore.QObject.disconnect(
                self.runner,
                QtCore.SIGNAL('done()'),
                self.aggregate)
        self.pbnShowQuestion.setVisible(True)
        self.grpQuestion.setEnabled(True)
        self.grpQuestion.setVisible(False)
        self.pbnRunStop.setEnabled(True)
        self.repaint()
        self.disable_busy_cursor()

    def aggregate(self):
        """Run all post processing steps.

        Called on self.runner SIGNAL('done()') starts aggregation steps.
        """
        LOGGER.debug('Do aggregation')
        if self.runner.impactLayer() is None:
            # Done was emitted, but no impact layer was calculated
            myResult = self.runner.result()
            myMessage = str(self.tr(
                'No impact layer was calculated. Error message: %1\n'
            ).arg(str(myResult)))
            myException = self.runner.lastException()
            if isinstance(myException, ZeroImpactException):
                myReport = m.Message()
                myReport.add(LOGO_ELEMENT)
                myReport.add(m.Heading(self.tr(
                    'Analysis Results'), **INFO_STYLE))
                myReport.add(m.Text(myException.message))
                myReport.add(m.Heading(self.tr('Notes'), **SUGGESTION_STYLE))
                myReport.add(m.Text(self.tr(
                    'It appears that no %1 are affected by %2. You may want '
                    'to consider:').arg(
                        self.cboExposure.currentText()).arg(
                            self.cboHazard.currentText()
                        )))
                myList = m.BulletedList()
                myList.add(self.tr(
                    'Check that you are not zoomed in too much and thus '
                    'excluding %1 from your analysis area.').arg(
                        self.cboExposure.currentText()))
                myList.add(self.tr(
                    'Check that the exposure is not no-data or zero for the '
                    'entire area of your analysis.'))
                myList.add(self.tr(
                    'Check that your impact function thresholds do not '
                    'exclude all features unintentionally.'))
                myReport.add(myList)
                self.show_static_message(myReport)
                self.hide_busy()
                return
            if myException is not None:
                myContext = self.tr(
                    'An exception occurred when calculating the results. %1'
                ).arg(self.runner.result())
                myMessage = get_error_message(myException, theContext=myContext)
            self.show_error_message(myMessage)
            self.analysisDone.emit(False)
            return

        try:
            self.aggregator.aggregate(self.runner.impactLayer())
        except Exception, e:  # pylint: disable=W0703
            # noinspection PyPropertyAccess
            e.args = (str(e.args[0]) + '\nAggregation error occurred',)
            raise

        #TODO (MB) do we really want this check?
        if self.aggregator.errorMessage is None:
            self.post_process()
        else:
            myContext = self.aggregator.errorMessage
            myException = AggregatioError(self.tr(
                'Aggregation error occurred.'))
            self.analysis_error(myException, myContext)

    def post_process(self):
        """Carry out any postprocessing required for this impact layer.
        """
        LOGGER.debug('Do postprocessing')
        self.postprocessorManager = PostprocessorManager(self.aggregator)
        self.postprocessorManager.functionParams = self.functionParams
        self.postprocessorManager.run()
        self.completed()
        self.analysisDone.emit(True)

    def enable_busy_cursor(self):
        """Set the hourglass enabled and stop listening for layer changes."""
        QtGui.qApp.setOverrideCursor(QtGui.QCursor(QtCore.Qt.WaitCursor))

    def disable_busy_cursor(self):
        """Disable the hourglass cursor and listen for layer changes."""
        QtGui.qApp.restoreOverrideCursor()

    def get_clip_parameters(self):
        """Calculate the best extents to use for the assessment.

        :returns: A tuple consisting of:

            * myExtraExposureKeywords: dict - any additional keywords that
                should be written to the exposure layer. For example if
                rescaling is required for a raster, the original resolution
                can be added to the keywords file.
            * myBufferedGeoExtent: list - [xmin, ymin, xmax, ymax] - the best
                extent that can be used given the input datasets and the
                current viewport extents.
            * myCellSize: float - the cell size that is the best of the
                hazard and exposure rasters.
            * myExposureLayer: QgsMapLayer - layer representing exposure.
            * myGeoExtent: list - [xmin, ymin, xmax, ymax] - the unbuffered
                intersection of the two input layers extents and the viewport.
            * myHazardLayer: QgsMapLayer - layer representing hazard.
        :rtype: dict, QgsRectangle, float,
                QgsMapLayer, QgsRectangle, QgsMapLayer
        :raises: InsufficientOverlapError
        """
        myHazardLayer = self.get_hazard_layer()
        myExposureLayer = self.get_exposure_layer()
        # Get the current viewport extent as an array in EPSG:4326
        myViewportGeoExtent = viewportGeoArray(self.iface.mapCanvas())
        # Get the Hazard extents as an array in EPSG:4326
        myHazardGeoExtent = extentToGeoArray(
            myHazardLayer.extent(),
            myHazardLayer.crs())
        # Get the Exposure extents as an array in EPSG:4326
        myExposureGeoExtent = extentToGeoArray(
            myExposureLayer.extent(),
            myExposureLayer.crs())

        # Reproject all extents to EPSG:4326 if needed
        myGeoCrs = QgsCoordinateReferenceSystem()
        myGeoCrs.createFromId(4326, QgsCoordinateReferenceSystem.EpsgCrsId)
        # Now work out the optimal extent between the two layers and
        # the current view extent. The optimal extent is the intersection
        # between the two layers and the viewport.
        try:
            # Extent is returned as an array [xmin,ymin,xmax,ymax]
            # We will convert it to a QgsRectangle afterwards.
            if self.clipToViewport:
                myGeoExtent = getOptimalExtent(myHazardGeoExtent,
                                               myExposureGeoExtent,
                                               myViewportGeoExtent)
            else:
                myGeoExtent = getOptimalExtent(myHazardGeoExtent,
                                               myExposureGeoExtent)

        except InsufficientOverlapError, e:
            # FIXME (MB): This branch is not covered by the tests
            myDescription = self.tr(
                'There was insufficient overlap between the input layers '
                'and / or the layers and the viewable area. Please select two '
                'overlapping layers and zoom or pan to them or disable '
                'viewable area clipping in the options dialog. Full details '
                'follow:')
            myMessage = m.Message(myDescription)
            myText = m.Paragraph(
                self.tr('Failed to obtain the optimal extent given:'))
            myMessage.add(myText)
            myList = m.BulletedList()
            # We must use Qt string interpolators for tr to work properly
            myList.add(
                self.tr('Hazard: %1').arg(
                    myHazardLayer.source()))

            myList.add(
                self.tr('Exposure: %1').arg(
                    myExposureLayer.source()))

            myList.add(
                self.tr('Viewable area Geo Extent: %1').arg(
                    QtCore.QString(str(myViewportGeoExtent))))

            myList.add(
                self.tr('Hazard Geo Extent: %1').arg(
                    QtCore.QString(str(myHazardGeoExtent))))

            myList.add(
                self.tr('Exposure Geo Extent: %1').arg(
                    QtCore.QString(str(myExposureGeoExtent))))

            myList.add(
                self.tr('Viewable area clipping enabled: %1').arg(
                    QtCore.QString(str(self.clipToViewport))))
            myList.add(
                self.tr('Details: %1').arg(
                    str(e)))
            myMessage.add(myList)
            raise InsufficientOverlapError(myMessage)

        # Next work out the ideal spatial resolution for rasters
        # in the analysis. If layers are not native WGS84, we estimate
        # this based on the geographic extents
        # rather than the layers native extents so that we can pass
        # the ideal WGS84 cell size and extents to the layer prep routines
        # and do all preprocessing in a single operation.
        # All this is done in the function getWGS84resolution
        myBufferedGeoExtent = myGeoExtent  # Bbox to use for hazard layer
        myCellSize = None
        myExtraExposureKeywords = {}
        if myHazardLayer.type() == QgsMapLayer.RasterLayer:
            # Hazard layer is raster
            myHazardGeoCellSize = getWGS84resolution(myHazardLayer)

            if myExposureLayer.type() == QgsMapLayer.RasterLayer:
                # In case of two raster layers establish common resolution
                myExposureGeoCellSize = getWGS84resolution(myExposureLayer)

                if myHazardGeoCellSize < myExposureGeoCellSize:
                    myCellSize = myHazardGeoCellSize
                else:
                    myCellSize = myExposureGeoCellSize

                # Record native resolution to allow rescaling of exposure data
                if not numpy.allclose(myCellSize, myExposureGeoCellSize):
                    myExtraExposureKeywords['resolution'] = \
                        myExposureGeoCellSize
            else:
                # If exposure is vector data grow hazard raster layer to
                # ensure there are enough pixels for points at the edge of
                # the view port to be interpolated correctly. This requires
                # resolution to be available
                if myExposureLayer.type() != QgsMapLayer.VectorLayer:
                    raise RuntimeError
                myBufferedGeoExtent = getBufferedExtent(myGeoExtent,
                                                        myHazardGeoCellSize)
        else:
            # Hazard layer is vector

            # In case hazard data is a point data set, we will not clip the
            # exposure data to it. The reason being that points may be used
            # as centers for evacuation circles: See issue #285
            if myHazardLayer.geometryType() == QGis.Point:
                myGeoExtent = myExposureGeoExtent

        return (
            myExtraExposureKeywords,
            myBufferedGeoExtent,
            myCellSize,
            myExposureLayer,
            myGeoExtent,
            myHazardLayer)

    def optimal_clip(self):
        """ A helper function to perform an optimal clip of the input data.
        Optimal extent should be considered as the intersection between
        the three inputs. The inasafe library will perform various checks
        to ensure that the extent is tenable, includes data from both
        etc.

        The result of this function will be two layers which are
        clipped and resampled if needed, and in the EPSG:4326 geographic
        coordinate reference system.

        Args:
            None
        Returns:
            A two-tuple containing the clipped hazard and exposure layers.

        Raises:
            Any exceptions raised by the InaSAFE library will be propagated.
        """

        # Get the hazard and exposure layers selected in the combos
        # and other related parameters needed for clipping.
        try:
            (myExtraExposureKeywords, myBufferedGeoExtent, myCellSize,
             myExposureLayer, myGeoExtent, myHazardLayer) = \
                self.get_clip_parameters()
        except:
            raise
        # Make sure that we have EPSG:4326 versions of the input layers
        # that are clipped and (in the case of two raster inputs) resampled to
        # the best resolution.
        myTitle = self.tr('Preparing hazard data')
        myDetail = self.tr(
            'We are resampling and clipping the hazard layer to match the '
            'intersection of the exposure layer and the current view extents.')
        myMessage = m.Message(
            m.Heading(myTitle, **PROGRESS_UPDATE_STYLE),
            m.Paragraph(myDetail))
        self.show_dynamic_message(myMessage)
        try:
            myClippedHazard = clipLayer(
                theLayer=myHazardLayer,
                theExtent=myBufferedGeoExtent,
                theCellSize=myCellSize,
                theHardClipFlag=self.clipHard)
        except CallGDALError, e:
            raise e
        except IOError, e:
            raise e

        myTitle = self.tr('Preparing exposure data')
        myDetail = self.tr(
            'We are resampling and clipping the exposure layer to match the '
            'intersection of the hazard layer and the current view extents.')
        myMessage = m.Message(
            m.Heading(myTitle, **PROGRESS_UPDATE_STYLE),
            m.Paragraph(myDetail))
        self.show_dynamic_message(myMessage)

        myClippedExposure = clipLayer(
            theLayer=myExposureLayer,
            theExtent=myGeoExtent,
            theCellSize=myCellSize,
            theExtraKeywords=myExtraExposureKeywords,
            theHardClipFlag=self.clipHard)
        return myClippedHazard, myClippedExposure

    def show_impact_keywords(self, myKeywords):
        """Show the keywords for an impact layer.

        .. note:: The print button will be enabled if this method is called.
            Also, the question group box will be hidden and the 'show
            question' button will be shown.

        :param myKeywords: A keywords dictionary.
        :type myKeywords: dict
        """
        LOGGER.debug('Showing Impact Keywords')
        if 'impact_summary' not in myKeywords:
            return

        myReport = m.Message()
        myReport.add(LOGO_ELEMENT)
        myReport.add(m.Heading(self.tr(
            'Analysis Results'), **INFO_STYLE))
        myReport.add(m.Text(myKeywords['impact_summary']))
        if 'postprocessing_report' in myKeywords:
            myReport.add(myKeywords['postprocessing_report'])
        myReport.add(impactLayerAttribution(myKeywords))
        self.pbnPrint.setEnabled(True)
        self.show_static_message(myReport)
        # also hide the question and show the show question button
        self.pbnShowQuestion.setVisible(True)
        self.grpQuestion.setEnabled(True)
        self.grpQuestion.setVisible(False)

    def show_generic_keywords(self, myKeywords):
        """Show the keywords defined for the active layer.

        .. note:: The print button will be disabled if this method is called.

        :param myKeywords: A keywords dictionary.
        :type myKeywords: dict
        """
        LOGGER.debug('Showing Generic Keywords')
        myReport = m.Message()
        myReport.add(LOGO_ELEMENT)
        myReport.add(m.Heading(self.tr(
            'Layer keywords:'), **INFO_STYLE))
        myReport.add(m.Text(self.tr(
            'The following keywords are defined for the active layer:')))
        self.pbnPrint.setEnabled(False)
        myList = m.BulletedList()
        for myKeyword in myKeywords:
            myValue = myKeywords[myKeyword]

            # Translate titles explicitly if possible
            if myKeyword == 'title':
                myValue = safeTr(myValue)
                # Add this keyword to report
            myKey = m.ImportantText(
                self.tr(myKeyword.capitalize()))
            myValue = str(myValue)
            myList.add(m.Text(myKey, myValue))

        myReport.add(myList)
        self.pbnPrint.setEnabled(False)
        self.show_static_message(myReport)

    def show_no_keywords_message(self):
        """Show a message indicating that no keywords are defined.

        .. note:: The print button will be disabled if this method is called.
        """
        LOGGER.debug('Showing No Keywords Message')
        myReport = m.Message()
        myReport.add(LOGO_ELEMENT)
        myReport.add(m.Heading(self.tr(
            'Layer keywords missing:'), **WARNING_STYLE))
        myContext = m.Message(
            m.Text(self.tr(
                'No keywords have been defined for this layer yet. If '
                'you wish to use it as an impact or hazard layer in a '
                'scenario, please use the keyword editor. You can open'
                ' the keyword editor by clicking on the ')),
            m.Image('qrc:/plugins/inasafe/show-keyword-editor.svg'),
            m.Text(self.tr(
                'icon in the toolbar, or choosing Plugins -> InaSAFE '
                '-> Keyword Editor from the menus.')))
        myReport.add(myContext)
        self.pbnPrint.setEnabled(False)
        self.show_static_message(myReport)

    def layer_changed(self, theLayer):
        """Handler for when the QGIS active layer is changed.
        If the active layer is changed and it has keywords and a report,
        show the report.

        :param theLayer: QgsMapLayer instance that is now active
        :type theLayer: QgsMapLayer, QgsRasterLayer, QgsVectorLayer

        """
        if theLayer is None:
            LOGGER.debug('Layer is None')
            return

        try:
            myKeywords = self.keywordIO.read_keywords(theLayer)

            if 'impact_summary' in myKeywords:
                self.show_impact_keywords(myKeywords)
            else:
                self.show_generic_keywords(myKeywords)

        except (KeywordNotFoundError,
                HashNotFoundError,
                InvalidParameterError), e:
            self.show_no_keywords_message()
            # Append the error message.
            myErrorMessage = get_error_message(e)
            self.show_error_message(myErrorMessage)
            return
        except Exception, e:
            myErrorMessage = get_error_message(e)
            self.show_error_message(myErrorMessage)
            return

    def save_state(self):
        """Save the current state of the ui to an internal class member.

        The saved state can be restored again easily using
        :func:`restore_state`
        """
        myStateDict = {
            'hazard': self.cboHazard.currentText(),
            'exposure': self.cboExposure.currentText(),
            'function': self.cboFunction.currentText(),
            'aggregation': self.cboAggregation.currentText(),
            'report': self.wvResults.page().currentFrame().toHtml()}
        self.state = myStateDict

    def restore_state(self):
        """Restore the state of the dock to the last known state."""
        if self.state is None:
            return
        for myCount in range(0, self.cboExposure.count()):
            myItemText = self.cboExposure.itemText(myCount)
            if myItemText == self.state['exposure']:
                self.cboExposure.setCurrentIndex(myCount)
                break
        for myCount in range(0, self.cboHazard.count()):
            myItemText = self.cboHazard.itemText(myCount)
            if myItemText == self.state['hazard']:
                self.cboHazard.setCurrentIndex(myCount)
                break
        for myCount in range(0, self.cboAggregation.count()):
            myItemText = self.cboAggregation.itemText(myCount)
            if myItemText == self.state['aggregation']:
                self.cboAggregation.setCurrentIndex(myCount)
                break
        self.restore_function_state(self.state['function'])
        self.wvResults.setHtml(self.state['report'])

    def restore_function_state(self, theOriginalFunction):
        """Restore the function combo to a known state.

        :param theOriginalFunction: Name of function that should be selected.
        :type theOriginalFunction: str

        """
        # Restore previous state of combo
        for myCount in range(0, self.cboFunction.count()):
            myItemText = self.cboFunction.itemText(myCount)
            if myItemText == theOriginalFunction:
                self.cboFunction.setCurrentIndex(myCount)
                break

    def print_map(self):
        """Slot to print map when print map button pressed."""
        myMap = Map(self.iface)
        if self.iface.activeLayer() is None:
            # noinspection PyCallByClass,PyTypeChecker
            QtGui.QMessageBox.warning(
                self,
                self.tr('InaSAFE'),
                self.tr('Please select a valid impact layer before '
                        'trying to print.'))
            return

        self.show_dynamic_message(
            m.Message(
                m.Heading(self.tr('Map Creator'), **PROGRESS_UPDATE_STYLE),
                m.Text(self.tr('Preparing map and report'))))

        myMap.setImpactLayer(self.iface.activeLayer())
        LOGGER.debug('Map Title: %s' % myMap.getMapTitle())
        myDefaultFileName = myMap.getMapTitle() + '.pdf'
        myDefaultFileName = myDefaultFileName.replace(' ', '_')
        # noinspection PyCallByClass,PyTypeChecker
        myMapPdfFilePath = QtGui.QFileDialog.getSaveFileName(
            self, self.tr('Write to PDF'),
            os.path.join(temp_dir(), myDefaultFileName),
            self.tr('Pdf File (*.pdf)'))
        myMapPdfFilePath = str(myMapPdfFilePath)

        if myMapPdfFilePath is None or myMapPdfFilePath == '':
            self.show_dynamic_message(
                m.Message(
                    m.Heading(self.tr('Map Creator'), **ERROR_MESSAGE_SIGNAL),
                    m.Text(self.tr('Printing cancelled!'))))
            return

        myTableFilename = os.path.splitext(myMapPdfFilePath)[0] + '_table.pdf'
        myHtmlRenderer = HtmlRenderer(thePageDpi=myMap.pageDpi)
        myKeywords = self.keywordIO.read_keywords(self.iface.activeLayer())
        myHtmlPdfPath = myHtmlRenderer.printImpactTable(
            myKeywords, theFilename=myTableFilename)

        try:
            myMap.printToPdf(myMapPdfFilePath)
        except Exception, e:  # pylint: disable=W0703
            # FIXME (Ole): This branch is not covered by the tests
            myReport = get_error_message(e)
            self.show_error_message(myReport)

        # Make sure the file paths can wrap nicely:
        myWrappedMapPath = myMapPdfFilePath.replace(os.sep, '<wbr>' + os.sep)
        myWrappedHtmlPath = myHtmlPdfPath.replace(os.sep, '<wbr>' + os.sep)
        myStatus = m.Message(
            m.Heading(self.tr('Map Creator'), **INFO_STYLE),
            m.Paragraph(self.tr(
                'Your PDF was created....opening using the default PDF viewer '
                'on your system. The generated pdfs were saved as:')),
            m.Paragraph(myWrappedMapPath),
            m.Paragraph(self.tr('and')),
            m.Paragraph(myWrappedHtmlPath))

        # noinspection PyCallByClass,PyTypeChecker,PyTypeChecker
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl('file:///' + myHtmlPdfPath,
                        QtCore.QUrl.TolerantMode))
        # noinspection PyCallByClass,PyTypeChecker,PyTypeChecker
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl('file:///' + myMapPdfFilePath,
                        QtCore.QUrl.TolerantMode))

        self.show_dynamic_message(myStatus)
        self.hide_busy()

    def get_function_id(self, theIndex=None):
        """Get the canonical impact function ID for the currently selected
           function (or the specified combo entry if theIndex is supplied.

        :param theIndex: Optional index position in the combo that you
            want the function id for. Defaults to None. If not set / None
            the currently selected combo item's function id will be
            returned.
        :type theIndex: int

        :returns: Id of the currently selected function.
        :rtype: str
        """
        if theIndex is None:
            myIndex = self.cboFunction.currentIndex()
        else:
            myIndex = theIndex
        myItemData = self.cboFunction.itemData(myIndex, QtCore.Qt.UserRole)
        myFunctionID = str(myItemData.toString())
        return myFunctionID

    def save_current_scenario(self, theScenarioFilePath=None):
        """Save current scenario to a text file.

        You can use the saved scenario with the batch runner.

        :param theScenarioFilePath: A path to the scenario file.
        :type theScenarioFilePath: str

        """
        LOGGER.info('saveCurrentScenario')
        warningTitle = self.tr('InaSAFE Save Scenario Warning')
        # get data layer
        # get absolute path of exposure & hazard layer, or the contents
        myExposureLayer = self.get_exposure_layer()
        myHazardLayer = self.get_hazard_layer()
        myAggregationLayer = self.get_aggregation_layer()
        myFunctionId = self.get_function_id(self.cboFunction.currentIndex())
        myMapCanvas = self.iface.mapCanvas()
        myExtent = myMapCanvas.extent()
        myExtentStr = str(myExtent.toString())
        myExtentStr = myExtentStr.replace(',', ', ')
        myExtentStr = myExtentStr.replace(' : ', ', ')

        # Checking f exposure and hazard layer is not None
        if myExposureLayer is None:
            warningMessage = self.tr(
                'Exposure layer is not found, can not save scenario. Please '
                'add exposure layer to do so.')
            # noinspection PyCallByClass,PyTypeChecker
            QtGui.QMessageBox.warning(self, warningTitle, warningMessage)
            return
        if myHazardLayer is None:
            warningMessage = self.tr(
                'Hazard layer is not found, can not save scenario. Please add '
                'hazard layer to do so.')
            # noinspection PyCallByClass,PyTypeChecker
            QtGui.QMessageBox.warning(self, warningTitle, warningMessage)
            return

        # Checking if function id is not None
        if myFunctionId == '' or myFunctionId is None:
            warningMessage = self.tr(
                'The impact function is empty, can not save scenario')
            # noinspection PyCallByClass,PyTypeChecker
            QtGui.QMessageBox.question(self, warningTitle, warningMessage)
            return

        myExposurePath = str(myExposureLayer.publicSource())
        myHazardPath = str(myHazardLayer.publicSource())

        myTitle = self.keywordIO.read_keywords(myHazardLayer, 'title')
        myTitle = safeTr(myTitle)

        myTitleDialog = self.tr('Save Scenario')
        # get last dir from setting
        mySettings = QSettings()
        lastSaveDir = mySettings.value('inasafe/lastSourceDir', '.')
        lastSaveDir = str(lastSaveDir.toString())
        if theScenarioFilePath is None:
            # noinspection PyCallByClass,PyTypeChecker
            myFileName = str(QFileDialog.getSaveFileName(
                self, myTitleDialog,
                os.path.join(lastSaveDir, myTitle + '.txt'),
                "Text files (*.txt)"))
        else:
            myFileName = theScenarioFilePath

        try:
            myRelExposurePath = os.path.relpath(myExposurePath, myFileName)
        except ValueError:
            myRelExposurePath = myExposurePath
        try:
            myRelHazardPath = os.path.relpath(myHazardPath, myFileName)
        except ValueError:
            myRelHazardPath = myHazardPath

        # write to file
        myParser = ConfigParser()
        myParser.add_section(myTitle)
        myParser.set(myTitle, 'exposure', myRelExposurePath)
        myParser.set(myTitle, 'hazard', myRelHazardPath)
        myParser.set(myTitle, 'function', myFunctionId)
        myParser.set(myTitle, 'extent', myExtentStr)

        if myAggregationLayer is not None:
            myAggregationPath = str(myAggregationLayer.publicSource())
            myRelAggregationPath = os.path.relpath(myAggregationPath,
                                                   myFileName)
            myParser.set(myTitle, 'aggregation', myRelAggregationPath)

        if myFileName is None or myFileName == '':
            return

        try:
            myParser.write(open(myFileName, 'at'))
            # Save directory settings
            lastSaveDir = os.path.dirname(myFileName)
            mySettings.setValue('inasafe/lastSourceDir', lastSaveDir)
        except IOError:
            # noinspection PyTypeChecker,PyCallByClass
            QtGui.QMessageBox.warning(
                self, self.tr('InaSAFE'),
                self.tr('Failed to save scenario to ' + myFileName))