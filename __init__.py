#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os
import shutil
import codecs
import getpass
from collections import namedtuple
from datetime import datetime
import binaryninjaui
from binaryninjaui import (getMonospaceFont, UIAction, UIActionHandler, Menu, UIContext)
if "qt_major_version" in binaryninjaui.__dict__ and binaryninjaui.qt_major_version == 6:
    from PySide6.QtWidgets import (QLineEdit, QPushButton, QApplication, QWidget,
         QVBoxLayout, QHBoxLayout, QDialog, QFileSystemModel, QTreeView, QLabel, QSplitter,
         QInputDialog, QMessageBox, QHeaderView, QKeySequenceEdit, QCheckBox)
    from PySide6.QtCore import (QDir, Qt, QFileInfo, QItemSelectionModel, QSettings, QUrl)
    from PySide6.QtGui import (QFontMetrics, QDesktopServices, QKeySequence, QIcon)
else:
    from PySide2.QtWidgets import (QLineEdit, QPushButton, QApplication, QWidget,
         QVBoxLayout, QHBoxLayout, QDialog, QFileSystemModel, QTreeView, QLabel, QSplitter,
         QInputDialog, QMessageBox, QHeaderView, QKeySequenceEdit, QCheckBox)
    from PySide2.QtCore import (QDir, Qt, QFileInfo, QItemSelectionModel, QSettings, QUrl)
    from PySide2.QtGui import (QFontMetrics, QDesktopServices, QKeySequence, QIcon)
from binaryninja import user_plugin_path, core_version
from binaryninja.plugin import BackgroundTaskThread
from binaryninja.log import (log_error, log_debug, log_alert, log_warn)
from binaryninja.settings import Settings
from binaryninja.interaction import get_directory_name_input
import numbers
from .QCodeEditor import QCodeEditor, Pylighter

Settings().register_group("snippets", "Snippets")
Settings().register_setting("snippets.syntaxHighlight", """
    {
        "title" : "Syntax highlighting for snippets",
        "type" : "boolean",
        "default" : true,
        "description" : "Whether to syntax highlight (may be performance problems with very large snippets and the current highlighting implementation.)",
        "ignore" : ["SettingsProjectScope", "SettingsResourceScope"]
    }
    """)
Settings().register_setting("snippets.indentation", """
    {
        "title" : "Indentation Syntax highlighting for snippets",
        "type" : "string",
        "default" : "    ",
        "description" : "String to use for indentation in snippets (tip: to use a tab, copy/paste a tab from another text field and paste here)",
        "ignore" : ["SettingsProjectScope", "SettingsResourceScope"]
    }
    """)


snippetPath = os.path.realpath(os.path.join(user_plugin_path(), "..", "snippets"))
try:
    if not os.path.exists(snippetPath):
        os.mkdir(snippetPath)
    dst_examples = os.path.join(snippetPath, "update_example_snippets.py")
    src_examples = os.path.join(os.path.dirname(os.path.realpath(__file__)), "update_example_snippets.py")
    if not os.path.exists(dst_examples):
        shutil.copy(src_examples, dst_examples)
except IOError:
    log_error("Unable to create %s or unable to add example updater, please report this bug" % snippetPath)


def includeWalk(dir, includeExt):
    filePaths = []
    for (root, dirs, files) in os.walk(dir):
        for f in files:
            if os.path.splitext(f)[1] in includeExt and '.git' not in root:
                filePaths.append(os.path.join(root, f))
    return filePaths


def loadSnippetFromFile(snippetPath):
    try:
        with codecs.open(snippetPath, 'r', 'utf-8') as snippetFile:
            snippetText = snippetFile.readlines()
    except:
        return ("", "", "")
    if (len(snippetText) < 3):
        return ("", "", "")
    else:
        qKeySequence = QKeySequence(snippetText[1].strip()[1:])
        if qKeySequence.isEmpty():
            qKeySequence = None
        return (snippetText[0].strip()[1:].strip(),
                qKeySequence,
                ''.join(snippetText[2:])
        )


def actionFromSnippet(snippetName, snippetDescription):
    if not snippetDescription:
        shortName = os.path.basename(snippetName)
        if shortName.endswith('.py'):
            shortName = shortName[:-3]
        return "Snippets\\" + shortName
    else:
        return "Snippets\\" + snippetDescription

def setupGlobals(context):
    snippetGlobals = {}
    snippetGlobals['current_view'] = context.binaryView
    snippetGlobals['bv'] = context.binaryView
    if not context.function:
        if not context.lowLevelILFunction:
            if not context.mediumLevelILFunction:
                snippetGlobals['current_hlil'] = None
                snippetGlobals['current_mlil'] = None
                snippetGlobals['current_function'] = None
                snippetGlobals['current_llil'] = None
            else:
                snippetGlobals['current_mlil'] = context.mediumLevelILFunction
                snippetGlobals['current_function'] = context.mediumLevelILFunction.source_function
                snippetGlobals['current_llil'] = context.mediumLevelILFunction.source_function.llil
                snippetGlobals['current_hlil'] = context.mediumLevelILFunction.source_function.hlil
        else:
            snippetGlobals['current_llil'] = context.lowLevelILFunction
            snippetGlobals['current_function'] = context.lowLevelILFunction.source_function
            snippetGlobals['current_mlil'] = context.lowLevelILFunction.source_function.mlil
            snippetGlobals['current_hlil'] = context.lowLevelILFunction.source_function.hlil
    else:
        snippetGlobals['current_function'] = context.function
        snippetGlobals['current_mlil'] = context.function.mlil
        snippetGlobals['current_hlil'] = context.function.hlil
        snippetGlobals['current_llil'] = context.function.llil
        snippetGlobals['current_token'] = context.function.llil

    if context.function is not None:
        snippetGlobals['current_basic_block'] = context.function.get_basic_block_at(context.address)
    else:
        snippetGlobals['current_basic_block'] = None
    snippetGlobals['current_address'] = context.address
    snippetGlobals['here'] = context.address
    if context.address is not None and isinstance(context.length, numbers.Integral):
        snippetGlobals['current_selection'] = (context.address, context.address+context.length)
    else:
        snippetGlobals['current_selection'] = None
    snippetGlobals['uicontext'] = context
    return snippetGlobals


def executeSnippet(code, description):
    #Get UI context, try currently selected otherwise default to the first one if the snippet widget is selected.
    ctx = UIContext.activeContext()
    dummycontext = {'binaryView': None, 'address': None, 'function': None, 'token': None, 'lowLevelILFunction': None, 'mediumLevelILFunction': None}
    if not ctx:
        ctx = UIContext.allContexts()[0]
    if not ctx:
        #There is no tab open at all but we still want other snippest to run that don't rely on context.
        context = namedtuple("context", dummycontext.keys())(*dummycontext.values())

    else:
        handler = ctx.contentActionHandler()
        if handler:
            context = handler.actionContext()
        else:
            context = namedtuple("context", dummycontext.keys())(*dummycontext.values())

    snippetGlobals = setupGlobals(context)

    SnippetTask(code, snippetGlobals, context, snippetName=description).start()


def makeSnippetFunction(code, description):
    return lambda context: executeSnippet(code, description)

# Global variable to indicate if analysis should be updated after a snippet is run
gUpdateAnalysisOnRun = False

class SnippetTask(BackgroundTaskThread):
    def __init__(self, code, snippetGlobals, context, snippetName="Executing snippet"):
        BackgroundTaskThread.__init__(self, f"{snippetName}...", False)
        self.code = code
        self.globals = snippetGlobals
        self.context = context

    def run(self):
        if self.context.binaryView:
            self.context.binaryView.begin_undo_actions()
        snippetGlobals = self.globals
        exec("from binaryninja import *", snippetGlobals)
        exec(self.code, snippetGlobals)
        if gUpdateAnalysisOnRun:
            exec("bv.update_analysis_and_wait()", snippetGlobals)
        if "here" in snippetGlobals and hasattr(self.context, "address") and snippetGlobals['here'] != self.context.address:
            self.context.binaryView.file.navigate(self.context.binaryView.file.view, snippetGlobals['here'])
        if "current_address" in snippetGlobals and hasattr(self.context, "address") and snippetGlobals['current_address'] != self.context.address:
            self.context.binaryView.file.navigate(self.context.binaryView.file.view, snippetGlobals['current_address'])
        if self.context.binaryView:
            self.context.binaryView.commit_undo_actions()


class Snippets(QDialog):

    def __init__(self, context, parent=None):
        super(Snippets, self).__init__(parent)
        # Create widgets
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.title = QLabel(self.tr("Snippet Editor"))
        self.saveButton = QPushButton(self.tr("&Save"))
        self.saveButton.setShortcut(QKeySequence(self.tr("Ctrl+S")))
        self.exportButton = QPushButton(self.tr("&Export to plugin"))
        self.exportButton.setShortcut(QKeySequence(self.tr("Ctrl+E")))
        self.runButton = QPushButton(self.tr("&Run"))
        self.runButton.setShortcut(QKeySequence(self.tr("Ctrl+R")))
        self.closeButton = QPushButton(self.tr("Close"))
        self.updateAnalysis = QCheckBox(self.tr("Update analysis when run"))
        self.clearHotkeyButton = QPushButton(self.tr("Clear Hotkey"))
        self.updateAnalysis.stateChanged.connect(self.setGlobalUpdateFlag)
        self.setWindowTitle(self.title.text())
        #self.newFolderButton = QPushButton("New Folder")
        self.browseButton = QPushButton("Browse Snippets")
        self.browseButton.setIcon(QIcon.fromTheme("edit-undo"))
        self.deleteSnippetButton = QPushButton("Delete")
        self.newSnippetButton = QPushButton("New Snippet")
        indentation = Settings().get_string("snippets.indentation")
        if Settings().get_bool("snippets.syntaxHighlight"):
            self.edit = QCodeEditor(SyntaxHighlighter=Pylighter, delimeter = indentation)
        else:
            self.edit = QCodeEditor(SyntaxHighlighter=None, delimeter = indentation)
        self.edit.setPlaceholderText("python code")
        self.resetting = False
        self.columns = 3
        self.context = context

        self.keySequenceEdit = QKeySequenceEdit(self)
        self.currentHotkey = QKeySequence()
        self.currentHotkeyLabel = QLabel("")
        self.currentFile = ""
        self.snippetName = QLineEdit()
        self.snippetName.setPlaceholderText("snippet filename")
        self.snippetDescription = QLineEdit()
        self.snippetDescription.setPlaceholderText("optional description")

        #Set Editbox Size
        font = getMonospaceFont(self)
        self.edit.setFont(font)
        font = QFontMetrics(font)
        self.edit.setTabStopDistance(4 * font.horizontalAdvance(' ')) #TODO, replace with settings API

        #Files
        self.files = QFileSystemModel()
        self.files.setRootPath(snippetPath)
        self.files.setNameFilters(["*.py"])

        #Tree
        self.tree = QTreeView()
        self.tree.setModel(self.files)
        self.tree.setSortingEnabled(True)
        self.tree.hideColumn(2)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.setRootIndex(self.files.index(snippetPath))
        for x in range(self.columns):
            #self.tree.resizeColumnToContents(x)
            self.tree.header().setSectionResizeMode(x, QHeaderView.ResizeToContents)
        treeLayout = QVBoxLayout()
        treeLayout.addWidget(self.tree)
        treeButtons = QHBoxLayout()
        #treeButtons.addWidget(self.newFolderButton)
        treeButtons.addWidget(self.browseButton)
        treeButtons.addWidget(self.newSnippetButton)
        treeButtons.addWidget(self.deleteSnippetButton)
        treeLayout.addLayout(treeButtons)
        treeWidget = QWidget()
        treeWidget.setLayout(treeLayout)

        # Create layout and add widgets
        optionsAndButtons = QVBoxLayout()

        options = QHBoxLayout()
        options.addWidget(self.clearHotkeyButton)
        options.addWidget(self.keySequenceEdit)
        options.addWidget(self.currentHotkeyLabel)
        options.addWidget(self.updateAnalysis)

        buttons = QHBoxLayout()
        buttons.addWidget(self.exportButton)
        buttons.addWidget(self.closeButton)
        buttons.addWidget(self.runButton)
        buttons.addWidget(self.saveButton)

        optionsAndButtons.addLayout(options)
        optionsAndButtons.addLayout(buttons)

        description = QHBoxLayout()
        description.addWidget(QLabel(self.tr("Filename: ")))
        description.addWidget(self.snippetName)
        description.addWidget(QLabel(self.tr("Description: ")))
        description.addWidget(self.snippetDescription)

        vlayoutWidget = QWidget()
        vlayout = QVBoxLayout()
        vlayout.addLayout(description)
        vlayout.addWidget(self.edit)
        vlayout.addLayout(optionsAndButtons)
        vlayoutWidget.setLayout(vlayout)

        hsplitter = QSplitter()
        hsplitter.addWidget(treeWidget)
        hsplitter.addWidget(vlayoutWidget)

        hlayout = QHBoxLayout()
        hlayout.addWidget(hsplitter)

        self.showNormal() #Fixes bug that maximized windows are "stuck"
        #Because you can't trust QT to do the right thing here
        if (sys.platform == "darwin"):
            self.settings = QSettings("Vector35", "Snippet Editor")
        else:
            self.settings = QSettings("Vector 35", "Snippet Editor")
        if self.settings.contains("ui/snippeteditor/geometry"):
            self.restoreGeometry(self.settings.value("ui/snippeteditor/geometry"))
        else:
            self.edit.setMinimumWidth(80 * font.averageCharWidth())
            self.edit.setMinimumHeight(30 * font.lineSpacing())

        # Set dialog layout
        self.setLayout(hlayout)

        # Add signals
        self.saveButton.clicked.connect(self.save)
        self.closeButton.clicked.connect(self.close)
        self.runButton.clicked.connect(self.run)
        self.exportButton.clicked.connect(self.export)
        self.clearHotkeyButton.clicked.connect(self.clearHotkey)
        self.tree.selectionModel().selectionChanged.connect(self.selectFile)
        self.newSnippetButton.clicked.connect(self.newFileDialog)
        self.deleteSnippetButton.clicked.connect(self.deleteSnippet)
        #self.newFolderButton.clicked.connect(self.newFolder)
        self.browseButton.clicked.connect(self.browseSnippets)

        if self.settings.contains("ui/snippeteditor/selected"):
            selectedName = self.settings.value("ui/snippeteditor/selected")
            self.tree.selectionModel().select(self.files.index(selectedName), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            if self.tree.selectionModel().hasSelection():
                self.selectFile(self.tree.selectionModel().selection(), None)
                self.edit.setFocus()
                cursor = self.edit.textCursor()
                cursor.setPosition(self.edit.document().characterCount()-1)
                self.edit.setTextCursor(cursor)
            else:
                self.readOnly(True)
        else:
            self.readOnly(True)

    def setGlobalUpdateFlag(self):
        """Update the "update analysis after run?" global variable."""
        global gUpdateAnalysisOnRun
        gUpdateAnalysisOnRun = self.updateAnalysis.isChecked()

    @staticmethod
    def registerAllSnippets():
        for action in list(filter(lambda x: x.startswith("Snippets\\"), UIAction.getAllRegisteredActions())):
            if action in ["Snippets\\Snippet Editor...", "Snippets\\Reload All Snippets"]:
                continue
            UIActionHandler.globalActions().unbindAction(action)
            Menu.mainMenu("Tools").removeAction(action)
            UIAction.unregisterAction(action)

        for snippet in includeWalk(snippetPath, ".py"):
            snippetKeys = None
            (snippetDescription, snippetKeys, snippetCode) = loadSnippetFromFile(snippet)
            actionText = actionFromSnippet(snippet, snippetDescription)
            if snippetCode:
                if snippetKeys == None:
                    UIAction.registerAction(actionText)
                else:
                    UIAction.registerAction(actionText, snippetKeys)
                UIActionHandler.globalActions().bindAction(actionText, UIAction(makeSnippetFunction(snippetCode, actionText)))
                Menu.mainMenu("Tools").addAction(actionText, "Snippets")

    def clearSelection(self):
        self.keySequenceEdit.clear()
        self.currentHotkey = QKeySequence()
        self.currentHotkeyLabel.setText("")
        self.snippetName.setText("")
        self.snippetDescription.setText("")
        self.edit.clear()
        self.tree.clearSelection()
        self.currentFile = ""

    def askSave(self):
        return QMessageBox.question(self, self.tr("Save?"), self.tr("Do you want to save changes to:\n\n{}?").format(self.snippetName.text()), QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)

    def reject(self):
        self.settings.setValue("ui/snippeteditor/geometry", self.saveGeometry())

        if self.snippetChanged():
            save = self.askSave()
            if save == QMessageBox.Yes:
                self.save()
            elif save == QMessageBox.No:
                self.loadSnippet()
            elif save == QMessageBox.Cancel:
                return
        self.accept()

    def browseSnippets(self):
        url = QUrl.fromLocalFile(snippetPath)
        QDesktopServices.openUrl(url)

    def newFolder(self):
        (folderName, ok) = QInputDialog.getText(self, self.tr("Folder Name"), self.tr("Folder Name: "))
        if ok and folderName:
            index = self.tree.selectionModel().currentIndex()
            selection = self.files.filePath(index)
            if QFileInfo(selection).isDir():
                QDir(selection).mkdir(folderName)
            else:
                QDir(snippetPath).mkdir(folderName)

    def selectFile(self, new, old):
        if (self.resetting):
            self.resetting = False
            return
        if len(new.indexes()) == 0:
            self.clearSelection()
            self.currentFile = ""
            self.readOnly(True)
            return
        newSelection = self.files.filePath(new.indexes()[0])
        self.settings.setValue("ui/snippeteditor/selected", newSelection)
        if QFileInfo(newSelection).isDir():
            self.readOnly(True)
            self.clearSelection()
            self.currentFile = ""
            return

        if old and old.length() > 0:
            oldSelection = self.files.filePath(old.indexes()[0])
            if not QFileInfo(oldSelection).isDir() and self.snippetChanged():
                save = self.askSave()
                if save == QMessageBox.Yes:
                    self.save()
                elif save == QMessageBox.No:
                    pass
                elif save == QMessageBox.Cancel:
                    self.resetting = True
                    self.tree.selectionModel().select(old, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                    return False

        self.currentFile = newSelection
        self.loadSnippet()

    def loadSnippet(self):
        (snippetDescription, snippetKeys, snippetCode) = loadSnippetFromFile(self.currentFile)
        self.snippetName.setText(os.path.basename(self.currentFile))
        self.snippetDescription.setText(snippetDescription) if snippetDescription else self.snippetDescription.setText("")
        self.keySequenceEdit.setKeySequence(snippetKeys) if snippetKeys else self.keySequenceEdit.setKeySequence(QKeySequence(""))
        self.edit.setPlainText(snippetCode) if snippetCode else self.edit.setPlainText("")
        self.readOnly(False)

    def newFileDialog(self):
        (snippetName, ok) = QInputDialog.getText(self, self.tr("Snippet Name"), self.tr("Snippet Name: "), flags=self.windowFlags())
        if ok and snippetName:
            if not snippetName.endswith(".py"):
                snippetName += ".py"
            index = self.tree.selectionModel().currentIndex()
            selection = self.files.filePath(index)
            if QFileInfo(selection).isDir():
                path = os.path.join(selection, snippetName)
            else:
                path = os.path.join(snippetPath, snippetName)
                self.readOnly(False)
            open(path, "w").close()
            self.tree.setCurrentIndex(self.files.index(path))
            log_debug("Snippets: Snippet %s created." % snippetName)

    def readOnly(self, flag):
        self.keySequenceEdit.setEnabled(not flag)
        self.snippetDescription.setReadOnly(flag)
        self.snippetName.setReadOnly(flag)
        self.edit.setReadOnly(flag)
        if flag:
            self.snippetDescription.setDisabled(True)
            self.snippetName.setDisabled(True)
            self.edit.setDisabled(True)
        else:
            self.snippetDescription.setEnabled(True)
            self.snippetName.setEnabled(True)
            self.edit.setEnabled(True)

    def deleteSnippet(self):
        selection = self.tree.selectedIndexes()[::self.columns][0] #treeview returns each selected element in the row
        snippetName = self.files.fileName(selection)
        question = QMessageBox.question(self, self.tr("Confirm"), self.tr("Confirm deletion: ") + snippetName)
        if (question == QMessageBox.StandardButton.Yes):
            log_debug("Snippets: Deleting snippet %s." % snippetName)
            self.clearSelection()
            self.files.remove(selection)
            self.registerAllSnippets()

    def snippetChanged(self):
        if (self.currentFile == "" or QFileInfo(self.currentFile).isDir()):
            return False
        (snippetDescription, snippetKeys, snippetCode) = loadSnippetFromFile(self.currentFile)
        if os.path.basename(self.currentFile) != self.snippetName.text():
            return True
        if snippetKeys == None and not self.keySequenceEdit.keySequence().isEmpty():
            return True
        if snippetKeys != None and snippetKeys != self.keySequenceEdit.keySequence().toString():
            return True
        return self.edit.toPlainText() != snippetCode or \
               self.snippetDescription.text() != snippetDescription

    def save(self):
        if os.path.basename(self.currentFile) != self.snippetName:
            #Renamed
            if not self.snippetName.text().endswith(".py") and not QMessageBox.question(self, self.tr("Rename?"), self.tr("Are you sure you want to rename?\n\n{} does not end in .py and you will not be able to rename back with snippets.").format(self.snippetName.text()), QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel) == QMessageBox.Yes:
                return
            os.unlink(self.currentFile)
            self.currentFile = os.path.join(os.path.dirname(self.currentFile), self.snippetName.text())
        log_debug("Snippets: Saving snippet %s" % self.currentFile)
        outputSnippet = codecs.open(self.currentFile, "w", "utf-8")
        outputSnippet.write("#" + self.snippetDescription.text() + "\n")
        outputSnippet.write("#" + self.keySequenceEdit.keySequence().toString() + "\n")
        outputSnippet.write(self.edit.toPlainText())
        outputSnippet.close()
        self.registerAllSnippets()

    def run(self):
        if self.context == None:
            log_warn("Cannot run snippets outside of the UI at this time.")
            return
        if self.snippetChanged():
            self.save()
        actionText = actionFromSnippet(self.currentFile, self.snippetDescription.text())
        UIActionHandler.globalActions().executeAction(actionText, self.context)

        log_debug("Snippets: Saving snippet %s" % self.currentFile)
        outputSnippet = codecs.open(self.currentFile, "w", "utf-8")
        outputSnippet.write("#" + self.snippetDescription.text() + "\n")
        outputSnippet.write("#" + self.keySequenceEdit.keySequence().toString() + "\n")
        outputSnippet.write(self.edit.toPlainText())
        outputSnippet.close()
        self.registerAllSnippets()

    def export(self):
        if self.snippetChanged():
            save = self.askSave()
            if save == QMessageBox.Yes:
                self.save()
            elif save == QMessageBox.No:
                self.loadSnippet()
            elif save == QMessageBox.Cancel:
                return

        folder = get_directory_name_input("Where would you like the plugin saved?", user_plugin_path())
        if self.snippetName.text() == "" or self.snippetDescription.text() == "":
            #TODO: Prompt user for description if missing
            log_alert("Snippets must have a name and description to be exported")
            return

        description = self.snippetDescription.text()
        name = self.snippetName.text()
        if name.endswith('.py'):
            name = name[:-3]
        #TODO: Form allowing input of multiple options such as user
        user = getpass.getuser()
        version = "2846"
        if core_version().count('.') == 2:
            version = core_version()[core_version().rfind('.')+1:core_version().rfind('.')+5]
        candidate = os.path.join(folder, name)

        if os.path.exists(candidate):
            overwrite = QMessageBox.question(self, self.tr("Folder already exists"), self.tr(f"That folder already exists, do you want to remove the folder first?\n{candidate}"), QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if overwrite == QMessageBox.Yes:
                log_debug("Snippets: Aborting export due to existing folder.")
                shutil.rmtree(candidate)
                self.save()
            elif overwrite == QMessageBox.Cancel:
                log_debug("Snippets: Aborting export due to existing folder.")
                return
        os.mkdir(candidate)

        #If no, continue just overwriting individual files.
        #TODO: License chooser from drop-down
        licenseText = f'''Copyright (c) {datetime.now().year} <{user}>

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
'''
        with open(os.path.join(candidate, "plugin.json"), 'w') as pluginjson:
            #not using f strings 'cause json isn't great for that
            pluginjson.write('''{
  "pluginmetadataversion": 2,
  "name": "''' + name + '''",
  "type": [
    "core",
    "ui",
    "architecture",
    "binaryview",
    "helper"
  ],
  "api": [
    "python3"
  ],
  "description": "''' + description + '''",
  "longdescription": "",
  "license": {
    "name": "MIT",
    "text": "''' + licenseText.replace("\n", "\\n") + '''"
  },
  "platforms": [
    "Darwin",
    "Linux",
    "Windows"
  ],
  "installinstructions": {
    "Darwin": "",
    "Linux": "",
    "Windows": ""
  },
  "dependencies": {
    "pip": [
      "",
    ],
  },
  "version": "1.0.0",
  "author": "''' + user + '''",
  "minimumbinaryninjaversion": ''' + version + '''
}
''')
        with open(os.path.join(candidate, "LICENSE"), 'w') as license:
            license.write(licenseText)

        #TODO: Optionally export plugin as UIPlugin with helpers established if
        #current_* appears anywhere in it
        with open(os.path.join(candidate, "__init__.py"), 'w') as initpy:
            if self.edit.toPlainText().count("\t") > self.edit.toPlainText().count("    "):
                delim = "\t"
            else:
                delim = "    " #not going to be any fancier than this for now, you get two choices
            pluginCode = delim + f'\n{delim}'.join(self.edit.toPlainText().split('\n'))
            if self.updateAnalysis.isChecked():
                update = f"{delim}bv.update_analysis_and_wait()"
            else:
                update = ""
            initpy.write(f"""from binaryninja import *

# Note that this is a sample plugin and you may need to manually edit it with
# additional functionality. In particular, this example only passes in the
# binary view. If you would like to act on an addres or function you should
# consider using other register_for* functions.

# Add documentation about UI plugin alternatives and potentially getting
# current_* functions

def main(bv):
{pluginCode}
{update}

PluginCommand.register('{name}', '{description}', main)

""")
        #TODO: Export README 

        longdescription='Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.  Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.'
        with open(os.path.join(candidate, "README.md"), 'w') as readme:
            readme.write(f'''# {name}
Author: **{user}** 

_{description}_

## Description:

{longdescription}

## Minimum Version

{version}

## License

This plugin is released under an [MIT license](./LICENSE).

## Metadata Version

2''')


        url = QUrl.fromLocalFile(candidate)
        QDesktopServices.openUrl(url)

    def clearHotkey(self):
        self.keySequenceEdit.clear()


snippets = None

def launchPlugin(context):
    global snippets
    if not snippets:
        snippets = Snippets(context, parent=context.widget)
    snippets.show()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    snippets = Snippets(None)
    snippets.show()
    sys.exit(app.exec_())
else:
    Snippets.registerAllSnippets()
    UIAction.registerAction("Snippets\\Snippet Editor...")
    UIAction.registerAction("Snippets\\Reload All Snippets")
    UIActionHandler.globalActions().bindAction("Snippets\\Snippet Editor...", UIAction(launchPlugin))
    UIActionHandler.globalActions().bindAction("Snippets\\Reload All Snippets", UIAction(Snippets.registerAllSnippets))
    Menu.mainMenu("Tools").addAction("Snippets\\Snippet Editor...", "Snippet")
    Menu.mainMenu("Tools").addAction("Snippets\\Reload All Snippets", "Snippet")
