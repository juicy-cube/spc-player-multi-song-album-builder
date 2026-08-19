import sys
import os
import shutil
import subprocess
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTableWidget, QTableWidgetItem, QAbstractItemView,
                             QFileDialog, QMessageBox, QHeaderView)
from PyQt6.QtCore import Qt

try:
    from spc_patcher import patch_spc
except ImportError:
    patch_spc = None

class SPCDragDropTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Title", "Author", "Game Name"])
        
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.spc_paths = []

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.lower().endswith('.spc'):
                self.add_spc_file(file_path)

    def add_spc_file(self, file_path):
        title, author, game = "Unknown", "Unknown", "Unknown"
        try:
            with open(file_path, 'rb') as f:
                f.seek(0x2E)
                title = f.read(32).split(b'\x00')[0].decode('ascii', errors='ignore').strip()
                f.seek(0x4E)
                game = f.read(32).split(b'\x00')[0].decode('ascii', errors='ignore').strip()
                f.seek(0xB1)
                author = f.read(32).split(b'\x00')[0].decode('ascii', errors='ignore').strip()
        except Exception:
            pass

        title = title if title else os.path.basename(file_path)
        
        row_position = self.rowCount()
        self.insertRow(row_position)
        self.setItem(row_position, 0, QTableWidgetItem(title))
        self.setItem(row_position, 1, QTableWidgetItem(author))
        self.setItem(row_position, 2, QTableWidgetItem(game))
        self.spc_paths.append(file_path)

class BuilderWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SNES SPC Album Builder")
        self.resize(800, 600)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Album Title:"))
        self.title_input = QLineEdit("SPC Player")
        header_layout.addWidget(self.title_input)
        layout.addLayout(header_layout)

        layout.addWidget(QLabel("Drag and drop SPC files below:"))
        self.table = SPCDragDropTable()
        layout.addWidget(self.table)

        list_btn_layout = QHBoxLayout()
        
        self.btn_up = QPushButton("Move Up")
        self.btn_up.clicked.connect(self.move_up)
        list_btn_layout.addWidget(self.btn_up)

        self.btn_down = QPushButton("Move Down")
        self.btn_down.clicked.connect(self.move_down)
        list_btn_layout.addWidget(self.btn_down)

        self.btn_del = QPushButton("Delete Selected")
        self.btn_del.clicked.connect(self.delete_row)
        list_btn_layout.addWidget(self.btn_del)
        
        layout.addLayout(list_btn_layout)

        action_btn_layout = QHBoxLayout()
        
        self.btn_load = QPushButton("LOAD album.txt")
        self.btn_load.clicked.connect(self.load_album)
        action_btn_layout.addWidget(self.btn_load)

        self.btn_build = QPushButton("BUILD ROM")
        self.btn_build.clicked.connect(self.build_rom)
        self.btn_build.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        action_btn_layout.addWidget(self.btn_build)

        layout.addLayout(action_btn_layout)

    def move_up(self):
        row = self.table.currentRow()
        if row > 0:
            self.swap_rows(row, row - 1)
            self.table.selectRow(row - 1)

    def move_down(self):
        row = self.table.currentRow()
        if row >= 0 and row < self.table.rowCount() - 1:
            self.swap_rows(row, row + 1)
            self.table.selectRow(row + 1)

    def delete_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.table.spc_paths.pop(row)

    def swap_rows(self, row1, row2):
        for col in range(self.table.columnCount()):
            item1 = self.table.takeItem(row1, col)
            item2 = self.table.takeItem(row2, col)
            self.table.setItem(row1, col, item2)
            self.table.setItem(row2, col, item1)
        self.table.spc_paths[row1], self.table.spc_paths[row2] = self.table.spc_paths[row2], self.table.spc_paths[row1]

    def load_album(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select album.txt file", "", "Text Files (*.txt)")
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            self.table.setRowCount(0)
            self.table.spc_paths.clear()

            current_block = []
            title_seen = False
            
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(";") or stripped.startswith("#"):
                    continue
                    
                if not title_seen and stripped.lower().startswith("title:"):
                    self.title_input.setText(stripped[len("title:"):].strip())
                    title_seen = True
                    continue
                
                if stripped == "":
                    if current_block:
                        self._add_block_to_table(current_block)
                        current_block = []
                else:
                    current_block.append(stripped)
                    
            if current_block:
                self._add_block_to_table(current_block)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load file: {e}")

    def _add_block_to_table(self, block):
        if len(block) >= 3:
            path = block[0]
            title = block[1]
            author = block[2]
            game = block[3] if len(block) >= 4 else ""
            
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(title))
            self.table.setItem(row, 1, QTableWidgetItem(author))
            self.table.setItem(row, 2, QTableWidgetItem(game))
            self.table.spc_paths.append(path)

    def build_rom(self):
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "Warning", "The song list is empty!")
            return

        if patch_spc is None:
            QMessageBox.critical(self, "Error", "File spc_patcher.py not found. This script is required.")
            return

        output_dir = "music"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        album_txt_path = os.path.join(output_dir, "album.txt")
        album_patched_txt_path = os.path.join(output_dir, "album_patched.txt")

        try:
            with open(album_txt_path, "w", encoding="utf-8") as f_orig, \
                 open(album_patched_txt_path, "w", encoding="utf-8") as f_patched:
                 
                album_title = self.title_input.text().strip()
                if album_title:
                    f_orig.write(f"title: {album_title}\n\n")
                    f_patched.write(f"title: {album_title}\n\n")

                for row in range(self.table.rowCount()):
                    orig_path = self.table.spc_paths[row]
                    title = self.table.item(row, 0).text()
                    author = self.table.item(row, 1).text()
                    game = self.table.item(row, 2).text()

                    new_filename = f"song{row+1}.spc"
                    target_path = os.path.join(output_dir, new_filename)
                    
                    shutil.copyfile(orig_path, target_path)
                    patch_spc(target_path, output_dir=output_dir)
                    
                    f_orig.write(f"{orig_path}\n")
                    f_orig.write(f"{title}\n")
                    f_orig.write(f"{author}\n")
                    if game:
                        f_orig.write(f"{game}\n")
                    f_orig.write("\n")

                    f_patched.write(f"{output_dir}/{new_filename}\n")
                    f_patched.write(f"{title}\n")
                    f_patched.write(f"{author}\n")
                    if game:
                        f_patched.write(f"{game}\n")
                    f_patched.write("\n")

            if os.path.exists("make_player.bat"):
                subprocess.run(["make_player.bat"], shell=True, check=True)
                QMessageBox.information(self, "Success", "ROM was built successfully!")
            else:
                QMessageBox.warning(self, "Partial Success", "Created album.txt and album_patched.txt, patched songs, but make_player.bat was not found.")

        except Exception as e:
            QMessageBox.critical(self, "Build Error", str(e))

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = BuilderWindow()
    window.show()
    sys.exit(app.exec())