import sys
import numpy as np
import pandas as pd
import os
import torch
import roifile
import zipfile

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QMainWindow
from PyQt6.QtWidgets import QTabWidget, QLineEdit, QScrollArea, QComboBox
from PyQt6.QtWidgets import QGridLayout, QSizePolicy, QStyleFactory, QTextEdit, QProgressBar, QSlider, QCheckBox
from PyQt6.QtGui import QPixmap, QImage, QFont, QIcon
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from skimage import measure
from cellpose import models
from functools import partial
from PIL import Image

from supplement import open_folder, image_preprocessing, analyze_segmented_cells, convert_to_pixmap, normalize_to_uint8, pixel_conversion
from toggle import ImageViewer

class ImageProcessingApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.model = models.Cellpose(gpu=True, model_type='cyto3')
        self.model.device = torch.device("mps")  # Force MPS usage (for mac gpu use)

        self.setWindowTitle("Toggle-Untoggle")
        self.setWindowIcon(QIcon("icon.png"))
        self.resize(800,600)
        self.showFullScreen()
        
        # Main tab widget With tabs on top
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.setCentralWidget(self.tabs)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.tabs.setStyleSheet("""
            QTabBar::tab {
                font-family: Arial;
                font-size: 16pt;  /* Adjust tab font size */
                padding: 2px;    /* Increase padding for bigger tabs */
                min-width: 200px; /* Set a minimum width for tabs */
                min-height: 30px; /* Set a minimum height for tabs */
                background-color: white;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
            QTabBar::tab:selected {
                background-color: lightblue;  /* Highlight selected tab */
            }
        """)

        # Customizing Tab for the input parameters
        self.input_tab = QWidget()
        self.create_input_form()
        self.tabs.addTab(self.input_tab, "Input Parameters")
        # List for storing grayscale images with interactive masks
        self.gray_viewers = []


    def create_slider(self, default_value, font_input):
        """
        Sliders for controlling pixel intensity params
        """
        label = QLabel(f"{default_value}")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(100)
        slider.setValue(default_value)
        # Update label when slider moves
        slider.valueChanged.connect(lambda value: label.setText(f"{value}"))
        # Creating a horizontal layout to hold both the slider and the label
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(slider)  # Adding the slider
        slider_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignLeft)  # Adding the label to the right
        # Setting font and styling for the label and slider
        label.setFont(font_input) 
        label.setFixedWidth(30)
        label.setStyleSheet("""
            QLabel {
                font-size: 9px;
                color: black;
                padding-left: 15px;  # Adding a little space between slider and label
            }
        """)
        slider.setFixedWidth(540) 
        # Container for the layout
        container = QWidget()
        container.setLayout(slider_layout)
        return container, slider
    
    def on_process_stop(self):
        """
        Enabling the button to stop the process
        """
        if self.worker is not None:
            self.worker.stop()  # Setting the abort flag to stop the worker
            self.processing_label.setText("Stopping...")

        if self.stop_button is not None:
            self.stop_button.setDisabled(True)  # Preventing accidental/multiple clicks

        if self.worker is None:
            # Enabling the process button again
            self.process_button.setEnabled(True)
            self.processing_in_progress = False

    def update_stop_button(self, count, button_layout):
        if count > 0 and (not hasattr(self, "stop_button") or self.stop_button is None):  # Making sure that the stop button is only created once
            self.stop_button = QPushButton("Stop Processing")
            self.stop_button.setStyleSheet("""
                QPushButton {
                    font-size: 20pt;  /* Bigger font size */
                    padding: 5px;    /* Add padding around the text */
                    background-color: #FFD1DC; /* Pale pink background */
                    color: black;     /* Black text */
                    border-radius: 5px; /* Rounded corners */
                    border: 1px solid #ddd; /* Border around button */
                }
                QPushButton:hover {
                    background-color: #FBC8D4;  /* Slightly lighter pink on hover */
                }
                QPushButton:pressed {
                    background-color: #FFB0C3;  /* Slightly darker pink when pressed */
                }
                """)
            self.stop_button.setFont(QFont("Arial", 25))
            self.stop_button.setFixedSize(200, 50)
            button_layout.addWidget(self.stop_button)
            button_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.stop_button.clicked.connect(self.on_process_stop)
            self.process_button.setEnabled(False)

    def on_process_clicked(self, button_layout):
        """
        Start image processing once the start button is clicked
        """
        if self.processing_in_progress:  
            return  # Preventing multiple app starts if it is already running
        self.processing_in_progress = True
        self.processing_label.setText("Processing started...")
        self.process_button.setEnabled(False)
        # Clearing the existing images tab before starting the processing
        self.stop_button = None 
        if self.images_tab is not None: 
            current_index = self.tabs.indexOf(self.images_tab)
            if current_index != -1:
                self.tabs.removeTab(current_index)
                self.images_tab = None
                self.image_layout = None
        # Starting processing after clearing the old tab
        self.start_processing()

        if self.worker is not None: # Stop buttons appears only if at least one image has been processed
            self.worker.image_processed.connect(lambda *args: 
                self.update_stop_button(self.worker.count, button_layout) if hasattr(self, "stop_button") and self.stop_button is None else None
            )

            self.worker.finished.connect(lambda: self.stop_button.deleteLater() if hasattr(self, "stop_button") and self.stop_button else None)

    def create_input_form(self):
        """
        Placeholder for images 
        """
        self.images_tab = None  # Start without images tab
        self.image_layout = None  # This will hold image layout
        main_layout = QHBoxLayout()  # Layout for aligning widgets to the left
        scroll_field = QScrollArea()
        scroll_field.setWidgetResizable(True) 

        # A widget that will contain all input elements
        input_container = QWidget()
        input_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(input_container)
        layout.setContentsMargins(40, 5, 0, 0)
        layout.setSpacing(5) # Spacing between text sections

        # Grid layout for structuring input sections and their corresponding labels
        grid_layout = QGridLayout()
        grid_layout.setContentsMargins(40, 0, 0, 0)
        grid_layout.setHorizontalSpacing(20)
        grid_layout.setVerticalSpacing(5)
        font_label = QFont("Arial", 18, QFont.Weight.Bold) # Font for the section labels
        font_input = QFont("Arial", 18) # Font for the input sections
        grid_layout.setColumnMinimumWidth(0, 300)  # Adjusting the minimum width for the first column (labels)
        grid_layout.setColumnStretch(5, 5)  # Letting the second column (inputs) stretch to take remaining space

        # Customizing the help button
        self.help_button = QPushButton("?")
        self.help_button.setFixedSize(40, 40)
        self.help_button.setFont(font_label)
        self.help_button.clicked.connect(self.toggle_help) # Connecting the button to a function that toggles help text visibility
        self.help_text = QTextEdit(parent=self)
        self.help_text.setText(self.get_help_text())
        font = QFont("Arial", 18) 
        self.help_text.setFont(font)
        self.help_text.setReadOnly(True)
        self.help_text.setVisible(False) 

        # Input fields with sliders
        self.main_marker_low_contrast_widget, self.main_marker_low_contrast_slider = self.create_slider(15, font_input)
        self.main_marker_high_contrast_widget, self.main_marker_high_contrast_slider = self.create_slider(99, font_input)
        self.nucleus_low_contrast_widget, self.nucleus_low_contrast_slider = self.create_slider(15, font_input)
        self.nucleus_high_contrast_widget, self.nucleus_high_contrast_slider = self.create_slider(99, font_input)

        # Creating a widget for a help button
        top_right_container = QWidget()
        top_right_layout = QVBoxLayout(top_right_container)
        top_right_layout.setContentsMargins(0, 0, 10, 0)  
        top_right_layout.addStretch()  # Pushung button to the right
        top_right_layout.addWidget(self.help_button)
        layout.addWidget(top_right_container)
        layout.setAlignment(top_right_container, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        # Input Fields
        self.images_folder_path = QLineEdit("")
        self.output_file = QLineEdit("single_cell_morphology")
        self.condition_name = QLineEdit("")
        self.rep_num = QLineEdit("")
        self.unique_main_marker_identifier = QLineEdit("")
        self.unique_nucleus_identifier = QLineEdit("")
        self.diameter = QLineEdit("20")
        self.flow_threshold = QLineEdit("0.4")
        self.min_area = QLineEdit("150")
        self.min_non_black_pixels_percentage = QLineEdit("10")
        self.intensity_threshold = QLineEdit("70")
        self.min_nucleus_pixels_percentage = QLineEdit("10")
        self.nucleus_pixel_threshold = QLineEdit("200")
        self.pixel_rate = QLineEdit("") 

        # Setting parameters for input fields
        for input_field in [
            self.images_folder_path, self.output_file, self.condition_name, self.rep_num, 
            self.unique_main_marker_identifier, self.unique_nucleus_identifier, self.diameter, self.flow_threshold,
            self.min_area, self.min_non_black_pixels_percentage, self.intensity_threshold,
            self.min_nucleus_pixels_percentage, self.nucleus_pixel_threshold, self.pixel_rate,
            self.main_marker_low_contrast_widget, self.main_marker_high_contrast_widget,
            self.nucleus_low_contrast_widget, self.nucleus_high_contrast_widget,
        ]:
            input_field.setFont(font_input)
            input_field.setFixedWidth(600)
            input_field.setFixedHeight(35)
            input_field.setStyleSheet("""
            QLineEdit {
                border: 1px solid gray;  /* Lighter gray border */
                border-radius: 3px;
                padding: 1px;
            }
        """)

        # Customizing dropdown menu for the segmentation marker color
        self.main_marker_channel_dropdown = QComboBox()
        self.main_marker_channel_dropdown.addItem("")  # Empty item as placeholder
        self.main_marker_channel_dropdown.addItems(["red", "green"])
        self.main_marker_channel_dropdown.setFont(font_input)
        self.main_marker_channel_dropdown.setFixedWidth(600)  
        self.main_marker_channel_dropdown.setStyleSheet("""
            QComboBox {
                border: 1px solid gray;
                border-radius: 1px;
                padding: 1px 20px 1px 3px;  /* Adjust padding to make room for wider arrow */
                min-width: 6em;
                background-color: white;
                selection-color:black;
                selection-background-color: lightblue;
            }
            QComboBox:!editable, QComboBox::drop-down:editable {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                            stop: 0 #FFFFFF, stop: 0.4 #FFFFFF,
                                            stop: 0.5 ##FFFFFF, stop: 1.0 #FFFFFF);
            }
            /* When popup is open */
            QComboBox:!editable:on, QComboBox::drop-down:editable:on {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                            stop: 0 #FFFFFF, stop: 0.4 #FFFFFF,
                                            stop: 0.5 #FFFFFF, stop: 1.0 #FFFFFF);
            }                                          
            /* Arrow inside dropdown */
            QComboBox::down-arrow {
                image: url(:/black_arrow.png);  /* Path to your arrow icon */
                width: 20px;  /* Set arrow width to 20px */
                height: 20px;  /* Optionally adjust arrow height */
            }
        """)
     
        # Creating a view for the dropdown menu
        dropdown_view = self.main_marker_channel_dropdown.view()
        dropdown_view.setFixedWidth(600)
        dropdown_view.setFixedHeight(100)

        # Adding rows to the grid layout
        def add_row(label_text, input_widget, row):
            label = QLabel(label_text)
            label.setFont(font_label)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid_layout.addWidget(label, row, 0)
            grid_layout.addWidget(input_widget, row, 1)

        # Adding each input field and label to the grid layout
        add_row("1. Images Folder Path:", self.images_folder_path, 0)
        add_row("2. Output File Name:", self.output_file, 1)
        add_row("3. Condition Name:", self.condition_name, 2)
        add_row("4. Replicate #:", self.rep_num, 3)
        add_row("5. Segmentation Channel File ID:", self.unique_main_marker_identifier, 4)
        add_row("6. Nucleus Channel File ID:", self.unique_nucleus_identifier, 5)
        add_row("7. Segmentation Channel Color:", self.main_marker_channel_dropdown, 6)
        add_row("8. Lower Percentile of Pixel Intensities for Segmentation Marker Channel:", self.main_marker_low_contrast_widget, 7)
        add_row("9. Upper Percentile of Pixel Intensities for Segmentation Marker Channel:", self.main_marker_high_contrast_widget, 8)
        add_row("10. Lower Percentile of Pixel Intensities for Nucleus Channel:", self.nucleus_low_contrast_widget, 9)
        add_row("11. Upper Percentile of Pixel Intensities for Nucleus Channel:", self.nucleus_high_contrast_widget, 10)
        add_row("12. Average Cell Diameter (µm):", self.diameter, 11)
        add_row("13. Flow Threshold:", self.flow_threshold, 12)
        add_row("14. Min Cell Area (µm²):", self.min_area, 13)
        add_row("15. Minimum Percentage of Image Occupied by Cells:", self.min_non_black_pixels_percentage, 14)
        add_row("16. Segmentation Channel Intensity Threshold:", self.intensity_threshold, 15)
        add_row("17. Minimum Percentage of Cell Area Occupied by Nucleus:", self.min_nucleus_pixels_percentage, 16)
        add_row("18. Nucleus Channel Intensity Threshold:", self.nucleus_pixel_threshold, 17)
        add_row("19. Pixel-to-Micron Ratio:", self.pixel_rate, 18)

        # Adding the grid layout to the main layout
        layout.addLayout(grid_layout)
                
        # Customizing the start button
        self.process_button = QPushButton("Process Images")
        self.process_button.setFont(QFont("Arial", 25))
        self.process_button.setFixedSize(200, 50)
        self.process_button.setStyleSheet("""
            QPushButton {
                font-size: 20pt;  /* Bigger font size */
                padding: 5px;    /* Add padding around the text */
                background-color: #ADD8E6; /* Light blue background */
                color: black;     /* Black text */
                border-radius: 5px; /* Rounded corners */
                border: 1px solid #ddd; /* Border around button */
            }
            QPushButton:hover {
                background-color: #87CEEB;  /* Slightly darker blue on hover */
            }
            QPushButton:pressed {
                background-color: #4682B4;  /* Even darker blue when pressed */
            }
        """)
        layout.addWidget(self.process_button, Qt.AlignmentFlag.AlignLeft)
        self.processing_label = QLabel("")
        self.processing_label.setStyleSheet(
            "color: green; font-size: 22px; font-weight: bold; padding-top: -20px;"
        )
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.process_button)
        button_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        button_layout.setContentsMargins(40, 10, 0, 0)
        layout.addLayout(button_layout)
        processing_layout = QHBoxLayout()
        processing_layout.addWidget(self.processing_label)
    
        self.processing_label.setFixedHeight(60) 
        processing_layout.setContentsMargins(40, 10, 0, 0) 
        layout.addLayout(processing_layout)  # Add layout to main layout

        # Progress bar settings
        self.processing_in_progress = False
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid #ADD8E6;  /* Light blue border */
                border-radius: 5px;
                background-color: white;  /* Background color */
                text-align: center;  /* Center align text */
                font-size: 18pt;  /* Larger text */
                color: black;  /* Black text */
                padding: 3px;
            }
            QProgressBar::chunk {
                background-color: lightblue; /* Light blue progress bar */
                width: 10px;
            }
        """)

        scroll_field.setWidget(input_container)
        main_layout.addWidget(scroll_field)
        self.process_button.clicked.connect(partial(self.on_process_clicked, button_layout))
        # Add the vertical layout to the main layout
        main_layout.addLayout(layout)
        self.input_tab.setLayout(main_layout)
       
    def clear_layout(self,layout):
        """
        Helper function to clear all widgets in a layout
        """
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def create_images_tab(self):
        """
        Create a new tab for images
        """
        images_tab = QWidget()
        self.tabs.addTab(images_tab, "Processed Images")  # Add the new tab to the widget
        layout = QVBoxLayout()

        # Scrollable area for images
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        image_container = QWidget()
        image_layout = QVBoxLayout()
        image_container.setLayout(image_layout)
        scroll_area.setWidget(image_container)
        image_container.setStyleSheet("background-color: white;")  
       
        # Checkboxes
        self.single_cell_checkbox = QCheckBox("Single-cell morphology")
        self.roi_checkbox = QCheckBox("ROIs")
        
        self.single_cell_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 18pt;  /* Adjust font size for the label */
                padding: 5px;  /* Optional, adjust for better spacing */
            }
            QCheckBox::indicator {
                width: 25px;  /* Adjust size of the checkbox */
                height: 25px;
            }
        """)

        self.roi_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 18pt;  /* Adjust font size for the label */
                padding: 5px;  /* Optional, adjust for better spacing */
            }
            QCheckBox::indicator {
                width: 25px;  /* Adjust size of the checkbox */
                height: 25px;
            }
        """)

        # Creating save button
        self.save_button = QPushButton("Save")
        self.save_button.setFixedSize(130, 40) 
        self.save_button.setStyleSheet("""
            QPushButton {
                font-size: 20pt;  /* Bigger font size */
                padding: 10px;    /* Add padding around the text */
                background-color: #ADD8E6; /* Light blue background */
                color: black;     /* Black text */
                border-radius: 5px; /* Rounded corners */
                border: 1px solid #ddd; /* Border around button */
            }
            QPushButton:hover {
                background-color: #87CEEB;  /* Slightly darker blue on hover */
            }
            QPushButton:pressed {
                background-color: #4682B4;  /* Even darker blue when pressed */
            }
        """)
        # Creating a QWidget to hold the button and label
        self.button_widget = QWidget()
        button_layout = QVBoxLayout(self.button_widget)
        button_layout.addWidget(self.single_cell_checkbox)
        button_layout.addWidget(self.roi_checkbox)
        button_layout.addWidget(self.save_button)
        button_layout.setSpacing(5)
        button_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self.save_button.clicked.connect(self.collect_all_callbacks)
        layout.addWidget(scroll_area)
        layout.addWidget(self.progress_bar)  # Adding progress bar below the scroll area
        images_tab.setLayout(layout)

        # Ensuring the layout is cleared before returning it
        self.clear_layout(image_layout)
        self.images_tab = images_tab  # Updating the reference to the new tab
        self.image_layout = image_layout 

        return image_layout 

    def collect_all_callbacks(self):
        """
        Main method to process mask callbacks and export data.
        Depending on the user selections, this function filters out inactive masks,
        saves the active properties to a CSV file, and/OR generates ROI ZIP files.
        """

        save_rois = self.roi_checkbox.isChecked()
        save_csv = self.single_cell_checkbox.isChecked()
        correct_props_df, excluded_props_df = self.filter_active_props(save_rois)
        if save_csv:
            self.save_props_to_csv(correct_props_df, excluded_props_df)
        if save_rois:
            self.save_rois_to_zip(correct_props_df)

    def filter_active_props(self, add_roi_name=False):

        """
        Filters the properties DataFrame to exclude untoggled objects.
        
        Parameters:
            add_roi_name (bool): If True, generates an 'roi_name' column for each object.
            
        Returns:
            Tuple[pd.DataFrame, pd.DataFrame]: A tuple containing the filtered (kept) DataFrame 
            and the excluded (removed) DataFrame.
        """
        
        all_props_df = self.worker.all_props_df.copy()

        # Identifying untoggled masks
        objects_to_remove = []
        for view in self.gray_viewers:
            for cb in view.callback_dict.values():
                if not cb['is_active']:
                    objects_to_remove.append(f"{cb['name']}{cb['label']}")

        all_props_df['filter'] = all_props_df['image_name'].astype(str) + all_props_df['label'].astype(str) 

        # Add roi_name for each object if applicable
        if add_roi_name:
            all_props_df['roi_name'] = all_props_df.apply(
                lambda row: f"{os.path.splitext(row['image_name'])[0]}_label{row['label']}.roi", axis=1
            )
        # Filtering out the untoggled cells from the dataframe
        correct = all_props_df[~all_props_df['filter'].isin(objects_to_remove)].drop(columns='filter')
        excluded = all_props_df[all_props_df['filter'].isin(objects_to_remove)].drop(columns='filter')
        
        return correct, excluded
    
    def save_props_to_csv(self, correct_df, excluded_df):

        """
        Saves filtered single-cell properties to CSV files.
        
        Parameters:
            correct_df (pd.DataFrame): DataFrame of active, valid objects to keep.
            excluded_df (pd.DataFrame): DataFrame of filtered-out (inactive) objects.
        """
        
        output_dir = self.images_folder_path.text()
        output_csv = os.path.join(output_dir, self.output_file.text() + '.csv')
        correct_df.to_csv(output_csv, index=False)

        if not excluded_df.empty:
            excluded_csv = os.path.join(output_dir, 'excluded_objects.csv')
            excluded_df.to_csv(excluded_csv, index=False)
    
    def save_rois_to_zip(self, correct_df):

        """
        Converts segmentation masks into ROI files and packages them into ZIP archives.
        
        Parameters:
            correct_df (pd.DataFrame): DataFrame containing valid objects to convert into ROIs.
        """
        roi_dir = os.path.join(self.images_folder_path.text(), "rois")
        os.makedirs(roi_dir, exist_ok=True)
        image_masks_dict = {}

        for _, row in correct_df.iterrows():
            image_name = row['image_name']
            label = row['label']
            mask_key = f"{image_name}{label}"

            if mask_key in self.worker.masks_dict:
                mask = self.worker.masks_dict[mask_key]["mask"]
                if image_name not in image_masks_dict:
                    shape = self.worker.image_shape
                    image_masks_dict[image_name] = np.zeros(shape, dtype=np.uint8)
                image_masks_dict[image_name][mask > 0] = label

        for image_name, full_mask in image_masks_dict.items():
            rotated_mask = np.rot90(np.flipud(full_mask), k=-1)
            roi_list = []
            labels = correct_df[correct_df["image_name"] == image_name]["label"].unique()

            for label in labels:
                binary_mask = (rotated_mask == label).astype(np.uint8)
                contours = measure.find_contours(binary_mask, 0.5)
                for contour in contours:
                    contour = np.round(contour).astype(np.int32)
                    if contour.shape[0] < 10:
                        continue
                    roi = roifile.ImagejRoi.frompoints(contour)
                    roi_filename = f"{os.path.splitext(image_name)[0]}_label{label}.roi"
                    roi_list.append((roi_filename, roi))

            if roi_list:
                zip_path = os.path.join(roi_dir, f"{os.path.splitext(image_name)[0]}.zip")
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for roi_filename, roi in roi_list:
                        zipf.writestr(roi_filename, roi.tobytes())

    def show_save_all(self):
        self.image_layout.addWidget(self.button_widget)

    def start_processing(self):
        try:
            folder_path = self.images_folder_path.text()
            output_file = self.output_file.text()
            condition_name = self.condition_name.text()
            rep_num = self.rep_num.text()
            main_marker_identifier = self.unique_main_marker_identifier.text()
            nucleus_identifier = self.unique_nucleus_identifier.text()
            color = self.main_marker_channel_dropdown.currentText()
            # Get values from sliders instead of text fields
            main_marker_contrast_low = self.main_marker_low_contrast_slider.value()
            main_marker_contrast_high = self.main_marker_high_contrast_slider.value()
            nucleus_contrast_low = self.nucleus_low_contrast_slider.value()
            nucleus_contrast_high = self.nucleus_high_contrast_slider.value()
            diam = int(self.diameter.text())
            flow_thresh = float(self.flow_threshold.text())
            min_area = int(self.min_area.text())
            min_non_black_pixels_percentage = float(self.min_non_black_pixels_percentage.text())
            intensity_threshold = int(self.intensity_threshold.text())
            min_nucleus_pixels_percentage = float(self.min_nucleus_pixels_percentage.text())
            nucleus_pixel_threshold = int(self.nucleus_pixel_threshold.text())
            pixel_conv_rate_text = self.pixel_rate.text()
            pixel_conv_rate = None

            # Loading images from folder
            self.images = open_folder(folder_path, [main_marker_identifier,nucleus_identifier])

            if pixel_conv_rate_text != "":
                try:
                    pixel_conv_rate = float(pixel_conv_rate_text)
                except ValueError:
                    self.update_status_label(f"Invalid input, please refer to instructions!")
                    self.processing_in_progress = False
                    self.process_button.setEnabled(True)  # Re-enable the start button

            # Creating the worker to process images in the background
            self.worker = ImageProcessingWorker(self.images, folder_path, condition_name, rep_num, main_marker_identifier, nucleus_identifier, color, 
                                                main_marker_contrast_low, main_marker_contrast_high, nucleus_contrast_low, nucleus_contrast_high, 
                                                diam, flow_thresh, min_area, min_non_black_pixels_percentage, intensity_threshold, min_nucleus_pixels_percentage,
                                            nucleus_pixel_threshold, pixel_conv_rate, output_file, self.progress_bar, self.model)

            # Connecting the worker's signal to the slot to update the UI
            self.worker.status_update.connect(self.update_status_label)
            self.worker.image_processed.connect(self.add_images_to_scrollable_area)
            self.worker.show_save_all.connect(self.show_save_all)
            self.worker.finished_processing.connect(self.processing_done)
            self.worker.progress_updated.connect(self.update_progress)

            # Starting the worker in the background
            self.worker.start()
            
        except ValueError as e:
            # Showing an error message if invalid input
            self.update_status_label(f"Invalid input, please refer to instructions!")
            self.processing_in_progress = False
            self.process_button.setEnabled(True)  # Re-enabling the start button

    def update_status_label(self, message):
        self.processing_label.setText(message)

    def processing_done(self):
        """
        Called when processing is finished
        """
        self.processing_in_progress = False
        self.process_button.setEnabled(True)
     
    def update_progress(self, value):
        """
        Update progress bar when processing is updated
        """
        self.progress_bar.setValue(value)  # Updating bar with percentage
        self.progress_bar.setFormat(f"{value}%")  # Display percentage
        self.progress_bar.repaint()  # Force UI update

    def add_images_to_scrollable_area(self, title, pixmap_gray, pixmap_rgb, pixmap_overlay, masks_list):

        container = QWidget()
        layout = QHBoxLayout()
        label_gray = QLabel()
        label_rgb = QLabel()
        label_overlay = QLabel()
        scaled_width = 450 # Target width for each image
        scaled_height = 550  # Target height for each image
        
        # Scale all images consistently
        scaled_pixmap_gray = pixmap_gray.scaled(scaled_width, scaled_height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled_pixmap_rgb = pixmap_rgb.scaled(scaled_width, scaled_height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled_pixmap_overlay = pixmap_overlay.scaled(scaled_width, scaled_height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

        label_rgb.setPixmap(scaled_pixmap_rgb)
        label_overlay.setPixmap(scaled_pixmap_overlay)
        label_gray.setPixmap(scaled_pixmap_gray)

        # Using the scaled gray image in ImageViewer
        gray_viewer = ImageViewer(scaled_pixmap_gray, masks_list)


        layout.addWidget(label_rgb)
        layout.addWidget(label_overlay)
        layout.addWidget(gray_viewer)
        container.setLayout(layout)

        self.gray_viewers.append(gray_viewer)

        # Creating a combined container for the title and image container
        combined_container = QWidget()
        combined_layout = QVBoxLayout()
        combined_layout.setSpacing(10)  
        combined_layout.addWidget(QLabel(f"<b><span style='font-size: 22px;'>{title}</span></b>"))
        combined_layout.addWidget(container)
        combined_container.setLayout(combined_layout)
        
        if self.image_layout is None:
            self.image_layout = self.create_images_tab()

        self.image_layout.addWidget(combined_container, alignment=Qt.AlignmentFlag.AlignTop)
    
    def toggle_help(self):
        """
        Show a large frameless help window, preserving min functionality
        """
        if self.help_text.isVisible():
            self.help_text.setVisible(False)
        else:
            self.help_text.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
            # Resize the window
            self.help_text.resize(800, 500)  
            self.help_text.setAlignment(Qt.AlignmentFlag.AlignLeft)

            # Center the help window within the parent widget
            parent_geometry = self.geometry()
            help_x = parent_geometry.x() + (parent_geometry.width() - self.help_text.width()) // 2
            help_y = parent_geometry.y() + (parent_geometry.height() - self.help_text.height()) // 2
            self.help_text.move(help_x, help_y)
            # Show the help window
            self.help_text.show()

    def mousePressEvent(self, event):
        """
        Close help text when clicking anywhere
        """
        if self.help_text.isVisible():
            self.help_text.setVisible(False)

    def get_help_text(self):
        """
        Return formatted help text
        """
        return (
            "1. Images Folder Path: The path to the folder containing the images. Only single-channel images should be present in the folder. If multi-channel display images with the same file IDs are present, you may get no images processed notice.\n\n"  # Add extra newline
            "2. Output File Name: Not a path, but the desired name for the output .csv file.\n\n"
            "3. Condition: Additional column with the specified condition.\n\n"
            "4. Replicate: Additional column with the replicate # specified.\n\n"
            "5. Unique Actin File Identifier: A keyword unique to actin images (e.g., d2, ch1).\n\n"
            "6. Unique Dapi File Identifier: A keyword unique to dapi images (e.g., d0, ch2).\n\n"
            "7. Actin Channel Color: The color of the actin channel (choose from dropdown).\n\n"
            "8. Lower Percentile for Segmentation Marker Channel: the lower percentile of pixel intensities for the segmentation marker image.Any intensity below this percentile is mapped to 0 (black). Contrast adjustments are there only for visualization purposes.Fluorescence intensity of original images is not modified, and all fluorescence data is extracted from raw images.\n\n"
            "9. Upper Percentile for Segmentation Marker Channel: the upper percentile of pixel intensities for the segmentation marker image.Any intensity above this percentile is mapped to 1 (white).\n\n"
            "10. Lower Percentile for Nucleus Channel: : the lower percentile of pixel intensities for the nucleus channel image.Any intensity below this percentile is mapped to 0 (black).\n\n"
            "11. Upper Percentile for Nucleus Channel: the upper percentile of pixel intensities for the nucleus channel image.Any intensity above this percentile is mapped to 1 (white).\n\n"
            "12. Average Cell Diameter: The typical cell diameter in microns.\n\n"
            "13. Flow Threshold: the maximum allowed error of the flows for each mask. Increase this threshold if segmentation is not returning as many ROIs as you expect. Similarly, decrease this threshold if cellpose is returning too many incorrect masks.\n\n"
            "14. Min Cell Area: The minimum area for a valid cell in microns.\n\n"
            "15. Minimum Percentage of Image Occupied by Cells: Increase if empty images appear.\n\n"
            "16. Actin Channel Intensity Threshold: Adjust to refine segmentation.\n\n"
            "17. Minimum Percentage of Cell Area Occupied by Nucleus: Adjust for better segmentation.\n\n"
            "18. Blue (DAPI) Pixel Threshold: Minimum fluorescence intensity for nucleus detection.\n\n"
            "19. Pixel Conversion Rate: The conversion factor from pixels to microns, varies across microscopes. For EVOS: 20x: 0.354, 40x: 0.18, 60x: 0.12\n"
        )
    
    # Override function
    def closeEvent(self, event):
        """
        Override the closeEvent to close the help window when the main window is closed
        """
        if self.help_text.isVisible():
            self.help_text.setVisible(False)  # Close help window before closing main window
        event.accept()  # Proceed with closing the main window


class ImageProcessingWorker(QThread):

    image_processed = pyqtSignal(str, QPixmap, QPixmap, QPixmap, list)
    status_update = pyqtSignal(str)  # New signal for status updates
    show_save_all = pyqtSignal() # Signal to show the save all button
    finished_processing = pyqtSignal() # Signal for the end of the process
    progress_updated = pyqtSignal(int) # Signal for a progress bar updates

    def __init__(self, images, folder_path, condition_name, rep_num, main_marker_identifier, nucleus_identifier,  color,  main_marker_contrast_low,
                 main_marker_contrast_high, nucleus_contrast_low, nucleus_contrast_high,  diam, thresh, min_area, min_non_black_pixels_percentage,
                 intensity_threshold, min_nucleus_pixels_percentage, nucleus_pixel_threshold, pixel_conv_rate, output_file, progress_bar, model):
        super().__init__()
        self.folder_path = folder_path
        self.images = images
        self.rep_num = rep_num
        self.condition_name = condition_name
        self.main_marker_identifier = main_marker_identifier
        self.nucleus_identifier = nucleus_identifier
        self.color = color
        self.main_marker_contrast_low = main_marker_contrast_low
        self.main_marker_contrast_high = main_marker_contrast_high
        self.nucleus_contrast_low = nucleus_contrast_low
        self.nucleus_contrast_high = nucleus_contrast_high
        self.diam = diam
        self.thresh = thresh
        self.min_area = min_area
        self.min_non_black_pixels_percentage = min_non_black_pixels_percentage
        self.intensity_threshold = intensity_threshold
        self.min_nucleus_pixels_percentage = min_nucleus_pixels_percentage
        self.nucleus_pixel_threshold = nucleus_pixel_threshold
        self.pixel_conv_rate = pixel_conv_rate
        self.output_file = output_file
        self.active = True
        self.count = 0  # Initialize count for processed images
        self.progress_bar = progress_bar
        self.model = model

    def stop(self):
        self.active = False
        
    def run(self):
        
        main_marker_channel_value = self.color
        
        if not self.active:  # Stop processing if active flag is false
            return
        
        # Checking if the folder path is empty or invalid
        if not self.folder_path or not os.path.isdir(self.folder_path):
            self.status_update.emit("Invalid folder path!")
            self.finished_processing.emit() 
            return
        
        # Checking if the selected color is empty (i.e., not selected)
        if self.condition_name == "":
            self.status_update.emit("Please input a condition name!")
            self.finished_processing.emit() 
            return 
        
        if self.rep_num == "":
            self.status_update.emit("Please input a replicate number!")
            self.finished_processing.emit() 
            return\

        if main_marker_channel_value == "":
            self.status_update.emit("Please select a valid segmentation marker channel color!")
            self.finished_processing.emit() 
            return
    
        if  not self.pixel_conv_rate:
            self.status_update.emit("Please input pixel-to-micron conversion rate")
            self.finished_processing.emit() 
            return
        
        # Ensuring 'self.images' is defined and contains the expected data
        if not hasattr(self, 'images') or not self.images:
            self.status_update.emit("No images loaded into the system!")
            self.finished_processing.emit() 
            return
        
        # Warning the user if they put in a path for an empty folder
        total_images = sum(1 for key in self.images if self.main_marker_identifier in key)
        if total_images == 0:
            self.status_update.emit("No images found in the folder. Check file IDs")
            self.finished_processing.emit() 
            return
        
        fail = True
        self.status_update.emit("Processing started...")
        self.all_props_df = pd.DataFrame()
        num_images = len(self.images)  # Getting total number of images
        self.masks_dict = {}  # Dictionary to store masks

        for num, (name, image) in enumerate(self.images.items()):
            if not self.active:
                break

            if self.main_marker_identifier in name and self.main_marker_identifier!="" and self.active:
                main_marker_image_name = name
                main_marker_image_path = image
                if not hasattr(self, "image_shape"):
                    image_array = np.array (Image.open(main_marker_image_path))
                    self.image_shape = image_array.shape
                nucleus_name = name.replace(self.main_marker_identifier, self.nucleus_identifier)

                if (nucleus_name not in self.images or self.nucleus_identifier=="") and (self.active):  # Prevent KeyError
                    self.status_update.emit(f"Missing nucleus image: {nucleus_name}")
                    self.finished_processing.emit()
                    return
                
                nucleus_image_path = self.images[nucleus_name]

                if self.active:
                    try:
                        # Processing images
                        if not self.active:
                            break
                        main_marker_image, nucleus_image, diamet, marker_channel_color, rgb = image_preprocessing(main_marker_image_path, nucleus_image_path,
                                                                                                    main_marker_channel = self.color,
                                                                                                    main_marker_contrast_high = self.main_marker_contrast_high,main_marker_contrast_low = self.main_marker_contrast_low,
                                                                                                    nucleus_contrast_low = self.nucleus_contrast_low, nucleus_contrast_high = self.nucleus_contrast_high, # image of the channel that will be used for segmentation purposes (red/actin channel used here)
                                                                                                    min_non_black_pixels_percentage = self.min_non_black_pixels_percentage,
                                                                                                    intensity_threshold=self.intensity_threshold,  pixel_conv_rate=self.pixel_conv_rate,
                                                                                                    diam = self.diam)
                        if not self.active:
                            break
                       
                        predicted_masks, _, _, _ = self.model.eval(main_marker_image, diameter=diamet, flow_threshold = self.thresh,  channels=[0, marker_channel_color])

                        if not self.active:
                            break
                        df, overlay_image, gray_image, masks_list = analyze_segmented_cells(predicted_masks, main_marker_image, main_marker_image_name,
                                                                                        nucleus_image, min_nucleus_pixels_percentage = self.min_nucleus_pixels_percentage,
                                                                                        nucleus_pixel_threshold=self.nucleus_pixel_threshold, 
                                                                                        pixel_conv_rate=self.pixel_conv_rate,
                                                                                        rgb_image = rgb, min_area = self.min_area,
                                                                                        condition_name= self.condition_name, replicate_num = self.rep_num)

                        if not self.active:
                            break

                        for mask, label in zip(masks_list, df['label']):
                            mask_key = f"{main_marker_image_name}{label}"
                            self.masks_dict[mask_key] = mask

                        if df is not None and self.active:
                            self.all_props_df = pd.concat([self.all_props_df, df], ignore_index=True)
                            # Normalizing images to uint8
                            if not self.active:
                                break
                            gray_image = normalize_to_uint8(gray_image)
                            rgb = normalize_to_uint8(rgb)  # Only normalizing if it's not in the range 0–255 already
                            overlay_image = normalize_to_uint8(overlay_image)

                            if not self.active:
                                break

                            # Converting processed images to QPixmap
                            pixmap_gray = convert_to_pixmap(gray_image, QImage.Format.Format_Grayscale8)
                            pixmap_rgb = convert_to_pixmap(np.concatenate([rgb, np.full((rgb.shape[0], rgb.shape[1], 1), 255, dtype=np.uint8)], axis=-1), QImage.Format.Format_RGBA8888)
                            pixmap_overlay = convert_to_pixmap(np.concatenate([overlay_image, np.full((overlay_image.shape[0], overlay_image.shape[1], 1), 255, dtype=np.uint8)], axis=-1), QImage.Format.Format_RGBA8888)
                            # Emitting signal to update the UI with the processed images
                            self.image_processed.emit(main_marker_image_name, pixmap_gray, pixmap_rgb, pixmap_overlay, masks_list)

                            self.count += 1

                    except Exception as e:
                        print(e)
                        continue  # Moving to the next image

            self.progress_updated.emit(int(((num + 1) / num_images) * 100))
          
        if (fail and self.active and self.count >= 1) or (not self.active):
            self.all_props_df = pixel_conversion(self.all_props_df, self.pixel_conv_rate)
            self.status_update.emit(f"Processing completed! {self.count} images processed.")
            self.show_save_all.emit()
            self.finished_processing.emit() 
        
        elif fail:
            self.status_update.emit("No images processed. Please check your input parameters.")
            self.finished_processing.emit() 
 

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    main_window = ImageProcessingApp()
    main_window.show()
    sys.exit(app.exec())