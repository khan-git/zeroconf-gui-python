from enum import Enum
from ipaddress import IPv4Address, IPv6Address
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QResizeEvent
from PyQt6.QtWidgets import QWidget
from zeroconf import BadTypeInNameException, ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *
import sys
import json

stylesheet = """
QMainWindow,
QWidget,
QDialog {
    background-color: white;
    color: black;
}

QTableWidget,
QTreeView {
    background-color: white;
    color: black;
}

QToolTip { 
    background-color: yellow; 
    color: black;
    border-style: outset;
    border-width: 2px;
    border-radius: 5px;
    padding: 3px;
}

QTreeView::branch:has-siblings: !adjoins-item {
    border-image: url(images/vline.png) 0;
}

QTreeView::branch:has-siblings:adjoins-item {
    border-image: url(images/branch-more.png) 0;
}

QTreeView::branch: !has-children: !has-siblings:adjoins-item {
    border-image: url(images/branch-end.png) 0;
}

QTreeView::branch:has-children: !has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {
    border-image: none;
    image: url(images/branch-closed.png);
}

QTreeView::branch:open:has-children: !has-siblings,
QTreeView::branch:open:has-children:has-siblings {
    border-image: none;
    image: url(images/branch-open.png);
}
"""


class Worker(QRunnable):

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        self.fn(*self.args, **self.kwargs)

class ListServices(QDialog):
    UPDATE = pyqtSignal(tuple)
    
    def __init__(self, parent, thread_pool: QThreadPool, types: list[str], types_filtered: list[str] = []) -> None:
        super().__init__(parent)

        self.setWindowTitle("Search for types")
        self.resize(QSize(200, 400))
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)
        self.types_boxes = []
        groups_box_label = QLabel("Types: ", self)
        self.layout.addWidget(groups_box_label)

        self.group_box = QGroupBox()
        self.box_layout = QVBoxLayout()
        self.group_box.setLayout(self.box_layout)
        self.all_checkbox = QCheckBox("Manage All")
        self.all_checkbox.setTristate(True)
        self.all_checkbox.stateChanged.connect(self.check_all)
        self.box_layout.addWidget(self.all_checkbox)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        self.box_layout.addWidget(line)
        self.layout.addWidget(self.group_box)
        for type in sorted(types):
            gCB = QCheckBox(type)
            gCB.setTristate(True)
            self.types_boxes.append(gCB)
            self.box_layout.addWidget(gCB)
            gCB.setCheckState(Qt.CheckState.PartiallyChecked)
            if type in types_filtered:
                gCB.setChecked(True)

        QBtn = QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        self.buttonBox = QDialogButtonBox(QBtn)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        self.layout.addWidget(self.buttonBox)

        self.UPDATE.connect(self.update)
        self.thread_pool = thread_pool
        self.worker = Worker(self.show_types)
        self.thread_pool.start(self.worker)
        self.resize(self.box_layout.sizeHint())

    @pyqtSlot()
    def check_all(self) -> None:
        gCB: QCheckBox
        for gCB in self.types_boxes:
            if gCB.checkState() is not Qt.CheckState.Checked or self.all_checkbox.checkState() is Qt.CheckState.Unchecked:
                gCB.setCheckState(self.all_checkbox.checkState())

    @pyqtSlot(tuple)
    def update(self, types: tuple) -> None:
        self.setWindowTitle("Select types")
        types_as_text = [ t.text() for t in self.types_boxes ]
        new_types_found = [t for t in types if t not in types_as_text]
        new_type: str
        for new_type in new_types_found:
            gCB = QCheckBox(new_type)
            gCB.setTristate(True)
            gCB.setChecked(self.all_checkbox.isChecked())
            self.types_boxes.append(gCB)
            self.box_layout.addWidget(gCB)
        self.resize(self.box_layout.sizeHint())

    def show_types(self):
        from zeroconf import ZeroconfServiceTypes
        types = ZeroconfServiceTypes.find()
        self.UPDATE.emit(types)


class ZeroconfListener(ServiceListener):

    class Event(Enum):
        UPDATE_SERVICE = 0
        REMOVE_SERVICE = 1
        ADD_SERVICE = 2

    def __init__(self, hook: callable) -> None:
        self._hook: callable = hook
        super().__init__()

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info: ServiceInfo = zc.get_service_info(type_, name)
        self._hook(self.Event.UPDATE_SERVICE, name, type_, info)
        # print(f"Service {name} updated: {type_}")

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._hook(self.Event.REMOVE_SERVICE, name, type_)
        # print(f"Service {name} removed {type_}")

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info: ServiceInfo = zc.get_service_info(type_, name)
        self._hook(self.Event.ADD_SERVICE, name, type_, info)
        # print(f"Service {name} added")
        # for key, value in info.properties.items():
        #     print(f"  {key} : {value}")


class ZeroConfGui(QMainWindow):
    UPDATE_SERVICE = pyqtSignal(str, str, ServiceInfo)
    REMOVE_SERVICE = pyqtSignal(str, str)
    ADD_SERVICE = pyqtSignal(str, str, ServiceInfo)

    def __init__(self):
        super().__init__()
        self._zeroconf = None
        self._browser = None
        self._listener = None

        self.setWindowTitle("ZeroConf GUI")
        self.setStyleSheet(stylesheet)
        self.resize(500,500)

        self.thread_pool = QThreadPool()

        self._settings = QSettings("ZeroConfGui", "ZeroConfGui")
        print(f'Settings file: {self._settings.fileName()}')
        self._services_expanded: [] = json.loads(self._settings.value('services_expanded', '[]'))

        # self._types: list = json.loads(self._settings.value('types', defaultValue='["_soap._tcp.local.", "_zmp._tcp.local."]'))
        self._types: set[str] = set(json.loads(self._settings.value('types', defaultValue='[]')))
        self._types_filtered: set[str] = set(json.loads(self._settings.value('types_filtered', defaultValue='{}')))

        self.UPDATE_SERVICE.connect(self.update_service)
        self.REMOVE_SERVICE.connect(self.remove_service)
        self.ADD_SERVICE.connect(self.add_service)

        # Move window to center of screen and slightly up
        qr = self.frameGeometry()
        cp:QPoint = self.screen().availableGeometry().center()
#        cp.setY(int(cp.y()/2))
        qr.moveCenter(cp)
        self.move(qr.topLeft())

        self.status_bar = self.statusBar()
        self.setup_menu()
        cWidget = QWidget()
        centralLayout = QHBoxLayout()
        cWidget.setLayout(centralLayout)
        
        centralLayout.addWidget(self.create_service_table())
        self.setCentralWidget(cWidget)

        self.start_listening(list(self._types_filtered))
        
    def start_listening(self, types: list[str]) -> None:
        """Restart listening for services"""
        if self._zeroconf:
            self._zeroconf.close()
        if self._browser:
            del self._browser
        if self._listener:
            del self._listener
        self.service_tree_model.removeRows(0, self.service_tree_model.rowCount())
        self._zeroconf = Zeroconf()
        self._listener = ZeroconfListener(self.hook)
        try : 
            self._browser = ServiceBrowser(self._zeroconf, types, self._listener)
        except BadTypeInNameException as ex:
            QMessageBox.warning(self, "ERROR", f"BadTypeInNameException:\n{ex}")

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        if self._zeroconf:
            self._zeroconf.close()
        print("Application closing")
        return super().closeEvent(a0)

    def setup_menu(self):
        """Set up menu"""
        mainMenu = self.menuBar()
        file_menu = mainMenu.addMenu('&File')

        quitAction = QAction("&Quit", self)
        quitAction.setShortcut("Ctrl+Q")
        quitAction.setStatusTip('Exit application')
        quitAction.triggered.connect(self.close)

        file_menu.addAction(quitAction)

        settings_menu = mainMenu.addMenu('&Settings')

        add_type_action = QAction("&Add type", self)
        add_type_action.setStatusTip('Add service type')
        add_type_action.triggered.connect(self.add_type)
        settings_menu.addAction(add_type_action)

        filter_types_action = QAction("&Filter types", self)
        filter_types_action.setStatusTip('Filter service type')
        filter_types_action.triggered.connect(self.filter_types)
        settings_menu.addAction(filter_types_action)

    def hook(self, event: ZeroconfListener.Event, name: str, type_: str, info: ServiceInfo = None) -> None:
        match event:
            case ZeroconfListener.Event.UPDATE_SERVICE:
                self.UPDATE_SERVICE.emit(name, type_, info)
            case ZeroconfListener.Event.REMOVE_SERVICE:
                self.REMOVE_SERVICE.emit(name, type_)
            case ZeroconfListener.Event.ADD_SERVICE:
                self.ADD_SERVICE.emit(name, type_, info)
            case _:
                print("ERROR: bad event")
    
    @pyqtSlot(str, str, ServiceInfo)
    def update_service(self, name: str, type_: str, info: ServiceInfo):
        items: list[QStandardItem] = self.service_tree_model.findItems(name)
        if len(items) > 1:
            QMessageBox.warning(self, "Warning", f"Multiple items found of {name}")
        if len(items) == 0:
            print(f"UPDATE: Item not found {name}")
            return
        item: QStandardItem = items[0]
        if item.hasChildren():
            kid: int
            for kid in range(item.rowCount()):
                self.service_tree_model.removeRow(kid, self.service_tree_model.indexFromItem(item))
        sibling: QStandardItem = self.service_tree_model.itemFromIndex(self.service_tree_model.sibling(item.row(), 0, self.service_tree_model.indexFromItem(item)))
        sibling.setText(f'{info.server}:{str(info.port)}')
        for key, value in info.decoded_properties.items():
            if key == '' or value is None:
                continue
            item.appendRow([QStandardItem(key), QStandardItem(value)])
        if name in self._services_expanded:
            self.service_tree.expand(self.service_tree_model.indexFromItem(item))
        self.items_changed()


    @pyqtSlot(str, str)
    def remove_service(self, name: str, type_: str):
        items: list[QStandardItem] = self.service_tree_model.findItems(name)
        item: QStandardItem
        for item in items:
            if item.hasChildren():
                kid: int
                for kid in range(item.rowCount()):
                    self.service_tree_model.removeRow(kid, self.service_tree_model.indexFromItem(item))


    @pyqtSlot(str, str, ServiceInfo)
    def add_service(self, name: str, type_: str, info: ServiceInfo):
        item = QStandardItem(name)
        self.service_tree_model.invisibleRootItem().appendRow([item,QStandardItem(f'{info.server}:{str(info.port)}')])
        if len(info._ipv4_addresses):
            addr4: IPv4Address
            for addr4 in info._ipv4_addresses:
                item.appendRow([QStandardItem("IPv4"), QStandardItem(str(addr4))])
        if len(info._ipv6_addresses):
            addr6: IPv6Address
            for addr6 in info._ipv6_addresses:
                item.appendRow([QStandardItem("IPv6"), QStandardItem(str(addr6))])

        key: bytes
        value: bytes
        for key, value in info.decoded_properties.items():
            if key == '' or value is None:
                continue
            item.appendRow([QStandardItem(key), QStandardItem(value)])
        if name in self._services_expanded:
            self.service_tree.expand(self.service_tree_model.indexFromItem(item))
        self.items_changed()

    @pyqtSlot()
    def add_type(self) -> None:
        """Add type to filter on"""
        type_str, ok = QInputDialog.getText(self, 'Add type', 'Type:')
        if ok:
            if type_str not in self._types:
                self._types.append(type_str)
                self._settings.setValue('types', json.dumps(self._types))
                self.start_listening(list(self._types_filtered))

    @pyqtSlot()
    def filter_types(self) -> None:
        """Filter types"""
        lDialog = ListServices(self, self.thread_pool, list(self._types), list(self._types_filtered))
        if lDialog.exec():
            self._types_filtered = set()
            type_box: QCheckBox
            for type_box in lDialog.types_boxes:
                if not type_box.text().endswith('.local.'):
                    continue
                if type_box.checkState() is Qt.CheckState.Unchecked and type_box.text() in self._types:
                    self._types.remove(type_box.text())
                if type_box.checkState()  is Qt.CheckState.Checked:
                        self._types_filtered.add(type_box.text())
                        self._types.add(type_box.text())
                if type_box.checkState() is Qt.CheckState.PartiallyChecked or type_box.checkState() is Qt.CheckState.Checked:
                        if type_box.text() not in self._types:
                            self._types.append(type_box.text())
        else:
            # Cancel selected
            return
        self._settings.setValue('types', json.dumps(list(self._types)))
        self._settings.setValue('types_filtered', json.dumps(list(self._types_filtered)))
        self.start_listening(list(self._types_filtered))

    def create_service_table(self) -> QGroupBox:
        """Create a TreeView"""
        box = QGroupBox("Services")
        bl = QHBoxLayout()
        box.setLayout(bl)
        self.box = box
        self.service_tree_model = QStandardItemModel(0, 3 , self)
        self.service_tree_model.setHeaderData(0, Qt.Orientation.Horizontal, "Name")
        self.service_tree_model.setHeaderData(1, Qt.Orientation.Horizontal, "Value")
        self.service_tree_model.setHeaderData(2, Qt.Orientation.Horizontal, "Empty") #Used to adjust view port
        self.service_tree_model.itemChanged.connect(self.items_changed)

        self.service_tree = QTreeView()
        self.service_tree.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.service_tree.setModel(self.service_tree_model)
        self.service_tree.setAnimated(True)
        self.service_tree.setIndentation(20)
        self.service_tree.setSortingEnabled(True)
        self.service_tree.setWindowTitle("Srvc View")
        self.service_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.service_tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        self.service_tree.expanded.connect(self.adjust_tree_columns)
        self.service_tree.collapsed.connect(self.adjust_tree_columns)
        bl.addWidget(self.service_tree)
        return box

    def items_changed(self, index: int = 0) -> None:
        num_expanded = 0
        for c in range(self.service_tree_model.rowCount()):
            row_index: QModelIndex = self.service_tree_model.index(c, 0)
            num_expanded += 1
            sz_row = self.service_tree.sizeHintForRow(c)
            if self.service_tree.isExpanded(row_index):
                num_expanded += self.service_tree_model.itemFromIndex(row_index).rowCount()
        for c in range(0, self.service_tree_model.columnCount()):
            self.service_tree.resizeColumnToContents(c)

        self.resize(self.service_tree.sizeHint().width(), 176 + sz_row * num_expanded)
        self.service_tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    @pyqtSlot()
    def adjust_tree_columns(self) -> None:
        self.items_changed()
        self.save_tree_expand()

    def save_tree_expand(self) -> None:
        self._services_expanded = []
        root: QStandardItem = self.service_tree_model.invisibleRootItem()
        for row in range(root.rowCount()):
            item: QStandardItem = root.child(row, column=0)
            if self.service_tree.isExpanded(self.service_tree_model.indexFromItem(item)):
                self._services_expanded.append(item.text())
        self._settings.setValue('services_expanded', json.dumps(list(set(self._services_expanded))))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    main_window = ZeroConfGui()
    main_window.show()
    sys.exit(app.exec())
