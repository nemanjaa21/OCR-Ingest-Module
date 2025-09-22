# -*- coding: utf-8 -*-
# Sample module in the public domain. Feel free to use this as a template
# for your modules (and you can remove this header and take complete credit
# and liability)
#
# Contact: Brian Carrier [carrier <at> sleuthkit [dot] org]
#
# This is free and unencumbered software released into the public domain.
#
# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.
#
# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.


# Ingest module for Autopsy with GUI
#
# Difference between other modules in this folder is that it has a GUI
# for user options. This is not needed for very basic modules. If you
# don't need a configuration UI, start with the other sample module.
#
# See http://sleuthkit.org/autopsy/docs/api-docs/latest/index.html for documentation


import jarray
import inspect
import subprocess
import os
import tempfile
from java.lang import System
from java.util.logging import Level
from javax.swing import JCheckBox
from javax.swing import BoxLayout
from javax.swing import JPanel
from javax.swing import JLabel
from javax.swing import JTextField
from javax.swing import JRadioButton
from javax.swing import ButtonGroup
from java.awt import GridBagLayout
from java.awt import GridBagConstraints
from org.sleuthkit.autopsy.casemodule import Case
from org.sleuthkit.autopsy.casemodule.services import Services
from org.sleuthkit.autopsy.ingest import DataSourceIngestModule
from org.sleuthkit.autopsy.ingest import FileIngestModule
from org.sleuthkit.autopsy.ingest import GenericIngestModuleJobSettings
from org.sleuthkit.autopsy.ingest import IngestMessage
from org.sleuthkit.autopsy.ingest import IngestModule
from org.sleuthkit.autopsy.ingest.IngestModule import IngestModuleException
from org.sleuthkit.autopsy.ingest import IngestModuleFactoryAdapter
from org.sleuthkit.autopsy.ingest import IngestModuleIngestJobSettings
from org.sleuthkit.autopsy.ingest import IngestModuleIngestJobSettingsPanel
from org.sleuthkit.autopsy.ingest import IngestServices
from org.sleuthkit.datamodel import BlackboardArtifact
from org.sleuthkit.datamodel import BlackboardAttribute
from org.sleuthkit.datamodel import ReadContentInputStream
from org.sleuthkit.autopsy.coreutils import Logger
from java.lang import IllegalArgumentException
from java.util import Arrays
from org.sleuthkit.datamodel import Score
from org.sleuthkit.autopsy.casemodule.services import Blackboard
from org.sleuthkit.datamodel import TskData

class OcrFileIngestModuleWithUIFactory(IngestModuleFactoryAdapter):
    def __init__(self):
        self.settings = None

    moduleName = "OCR Ingest Module (Tesseract)"

    def getModuleDisplayName(self):
        return self.moduleName

    def getModuleDescription(self):
        return "Performs Tesseract OCR on images and saves the result to the blackboard for keyword searching."

    def getModuleVersionNumber(self):
        return "4.10"

    def getDefaultIngestJobSettings(self):
        return GenericIngestModuleJobSettings()

    def hasIngestJobSettingsPanel(self):
        return True

    def getIngestJobSettingsPanel(self, settings):
        if not isinstance(settings, GenericIngestModuleJobSettings):
            raise IllegalArgumentException("Expected settings argument to be instanceof GenericIngestModuleJobSettings")
        self.settings = settings
        return OcrFileIngestModuleWithUISettingsPanel(self.settings)

    def isFileIngestModuleFactory(self):
        return True

    def createFileIngestModule(self, ingestOptions):
        return OcrFileIngestModuleWithUI(self.settings)

class OcrFileIngestModuleWithUI(FileIngestModule):

    _logger = Logger.getLogger(OcrFileIngestModuleWithUIFactory.moduleName)

    def log(self, level, msg):
        self._logger.logp(level, self.__class__.__name__, inspect.stack()[1][3], msg)

    def __init__(self, settings):
        self.local_settings = settings
        self.supported_extensions = []
        self.ingestServices = IngestServices.getInstance()
        self.filesFound = 0
        self.context = None

    def startUp(self, context):
        self.context = context
        if self.local_settings.getSetting("jpg_flag") == "true":
            self.supported_extensions.append(".jpg")
            self.supported_extensions.append(".jpeg")
        if self.local_settings.getSetting("png_flag") == "true":
            self.supported_extensions.append(".png")
        if self.local_settings.getSetting("tif_flag") == "true":
            self.supported_extensions.append(".tif")
            self.supported_extensions.append(".tiff")
        if self.local_settings.getSetting("bmp_flag") == "true":
            self.supported_extensions.append(".bmp")
        if self.local_settings.getSetting("gif_flag") == "true":
            self.supported_extensions.append(".gif")

        if not self.supported_extensions:
            self.log(Level.INFO, "No image types selected. Module will not run on any files.")

    def process(self, file):
        if ((file.getType() == TskData.TSK_DB_FILES_TYPE_ENUM.UNALLOC_BLOCKS) or
            (file.getType() == TskData.TSK_DB_FILES_TYPE_ENUM.UNUSED_BLOCKS) or
            (file.isFile() == False)):
            return IngestModule.ProcessResult.OK

        if file.getName().lower().endswith(tuple(self.supported_extensions)):
            self.log(Level.INFO, "Processing file: " + file.getName())

            temp_file_path = None
            processed_file_path = None
            try:
                # Step 1: Save the original image to a temporary file
                temp_fd, temp_file_path = tempfile.mkstemp(suffix=os.path.splitext(file.getName())[1])
                os.close(temp_fd)
                
                with open(temp_file_path, 'wb') as temp_file:
                    buffer = jarray.zeros(8192, "b")
                    inputStream = ReadContentInputStream(file)
                    
                    bytes_read = inputStream.read(buffer)
                    while bytes_read != -1:
                        temp_file.write(buffer[:bytes_read])
                        bytes_read = inputStream.read(buffer)
                
                self.log(Level.INFO, "Temporary original image file saved to: " + temp_file_path)

                # Step 2: Apply image preprocessing with ImageMagick
                processed_fd, processed_file_path = tempfile.mkstemp(suffix=".png")
                os.close(processed_fd)

                # Dynamically build the command based on user options
                magick_args = []
                magick_args.append('magick')
                magick_args.append(temp_file_path)
                
                # Grayscale
                if self.local_settings.getSetting("grayscale_flag") == "true":
                    magick_args.append('-grayscale')
                    magick_args.append('Rec709Luminance')

                # Resize
                if self.local_settings.getSetting("skip_resize_flag") == "false":
                    resize_value = self.local_settings.getSetting("resize_value")
                    if resize_value:
                        magick_args.append('-resize')
                        magick_args.append(resize_value + '%')

                magick_args.append(processed_file_path)

                convert_process = subprocess.Popen(magick_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                convert_stdout, convert_stderr = convert_process.communicate()
                convert_return_code = convert_process.wait()

                if convert_return_code != 0:
                    self.log(Level.WARNING, "ImageMagick conversion failed for file " + file.getName() + ". Error: " + convert_stderr)
                    return IngestModule.ProcessResult.OK
                
                self.log(Level.INFO, "Image preprocessed with ImageMagick and saved to: " + processed_file_path)

                # Step 3: Run Tesseract on the processed image
                tesseract_cmd = ['tesseract', processed_file_path, 'stdout']
                
                language_code = self.local_settings.getSetting("language_code")
                if language_code:
                    tesseract_cmd.append('-l')
                    tesseract_cmd.append(language_code)
                
                tesseract_process = subprocess.Popen(tesseract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                tesseract_stdout, tesseract_stderr = tesseract_process.communicate()
                tesseract_return_code = tesseract_process.wait()

                if tesseract_return_code != 0:
                    self.log(Level.WARNING, "Tesseract failed to process file " + file.getName() + ". Error: " + tesseract_stderr)
                    return IngestModule.ProcessResult.OK

                # MODIFIED: Decode the stdout byte stream to a UTF-8 string
                ocr_text = tesseract_stdout.decode('utf-8', 'ignore').strip()
                self.log(Level.INFO, "OCR text extracted from " + file.getName())
                self.log(Level.INFO, "Text which was extracted ====== " + ocr_text )
                self.log(Level.INFO, "==================================")

                if ocr_text:
                    self.filesFound += 1
                    try:
                        blackboard = Case.getCurrentCase().getSleuthkitCase().getBlackboard()
                        
                        attrs = Arrays.asList( BlackboardAttribute(BlackboardAttribute.Type.TSK_KEYWORD,
                                                                   OcrFileIngestModuleWithUIFactory.moduleName, ocr_text))
                        art = file.newAnalysisResult(BlackboardArtifact.Type.TSK_KEYWORD_HIT, Score.SCORE_LIKELY_NOTABLE, None, "Text Files", None, attrs).getAnalysisResult()

                        blackboard.postArtifact(art, OcrFileIngestModuleWithUIFactory.moduleName, self.context.getJobId())

                        message = IngestMessage.createMessage(
                                 IngestMessage.MessageType.DATA,
                                 OcrFileIngestModuleWithUIFactory.moduleName,
                                 "OCR text found and saved for " + file.getName() )
                        
                        self.ingestServices.postMessage(message)
                    except Blackboard.BlackboardException as e:
                        self.log(Level.SEVERE, "Error posting artifacts to blackboard for " + file.getName() + ": " + str(e))
                    except Exception as ex:
                        self.log(Level.SEVERE, "Error creating blackboard artifact: " + str(ex))
                else:
                    self.log(Level.INFO, "No text found in " + file.getName())
                    self.ingestServices.postMessage(
                        IngestMessage.createMessage(
                            IngestMessage.MessageType.INFO,
                            OcrFileIngestModuleWithUIFactory.moduleName,
                            "No text found in " + file.getName()
                        )
                    )

            except subprocess.CalledProcessError as e:
                self.log(Level.WARNING, "Subprocess call failed. Error: " + e.stderr)
            except IOError:
                self.log(Level.SEVERE, "ImageMagick 'magick' or Tesseract executable not found. Please ensure they are in your system's PATH.")
            except Exception as e:
                self.log(Level.SEVERE, "An unexpected error occurred during OCR: " + str(e))
            finally:
                # Step 4: Clean up both temporary files
                if temp_file_path and os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                if processed_file_path and os.path.exists(processed_file_path):
                    os.remove(processed_file_path)

        return IngestModule.ProcessResult.OK

    def shutDown(self):
        message = IngestMessage.createMessage(
            IngestMessage.MessageType.DATA, 
            OcrFileIngestModuleWithUIFactory.moduleName,
            "OCR module finished. " + str(self.filesFound) + " images processed."
        )
        self.ingestServices.postMessage(message)

class OcrFileIngestModuleWithUISettingsPanel(IngestModuleIngestJobSettingsPanel):
    def __init__(self, settings):
        self.local_settings = settings
        self.initComponents()
        self.customizeComponents()

    def checkboxJpgEvent(self, event):
        self.local_settings.setSetting("jpg_flag", "true" if self.checkboxJpg.isSelected() else "false")
    
    def checkboxPngEvent(self, event):
        self.local_settings.setSetting("png_flag", "true" if self.checkboxPng.isSelected() else "false")

    def checkboxTifEvent(self, event):
        self.local_settings.setSetting("tif_flag", "true" if self.checkboxTif.isSelected() else "false")

    def checkboxBmpEvent(self, event):
        self.local_settings.setSetting("bmp_flag", "true" if self.checkboxBmp.isSelected() else "false")

    def checkboxGifEvent(self, event):
        self.local_settings.setSetting("gif_flag", "true" if self.checkboxGif.isSelected() else "false")

    def checkboxGrayscaleEvent(self, event):
        self.local_settings.setSetting("grayscale_flag", "true" if self.checkboxGrayscale.isSelected() else "false")
    
    def checkboxSkipResizeEvent(self, event):
        skip_resize_flag = self.checkboxSkipResize.isSelected()
        self.local_settings.setSetting("skip_resize_flag", "true" if skip_resize_flag else "false")
        
        # Enable/disable radio buttons based on the checkbox state
        self.resize25.setEnabled(not skip_resize_flag)
        self.resize50.setEnabled(not skip_resize_flag)
        self.resize75.setEnabled(not skip_resize_flag)
        self.resize100.setEnabled(not skip_resize_flag)
        
        # Set a default value if the checkbox is unchecked
        if not skip_resize_flag:
            current_resize_value = self.local_settings.getSetting("resize_value")
            if not current_resize_value:
                self.resize100.setSelected(True)
                self.local_settings.setSetting("resize_value", "100")

    def resizeEvent(self, event):
        self.local_settings.setSetting("resize_value", event.getSource().getText().strip('%'))
    
    # ADDED: Event handler for language radio buttons
    def languageEvent(self, event):
        if self.language_eng.isSelected():
            self.local_settings.setSetting("language_code", "eng")
        elif self.language_srp.isSelected():
            self.local_settings.setSetting("language_code", "srp")
        elif self.language_deu.isSelected():
            self.local_settings.setSetting("language_code", "deu")

    def initComponents(self):
        self.setLayout(BoxLayout(self, BoxLayout.Y_AXIS))
        
        self.image_types_label = JLabel("Image file formats:")
        self.checkboxJpg = JCheckBox("Process JPG/JPEG images", actionPerformed=self.checkboxJpgEvent)
        self.checkboxPng = JCheckBox("Process PNG images", actionPerformed=self.checkboxPngEvent)
        self.checkboxTif = JCheckBox("Process TIFF images", actionPerformed=self.checkboxTifEvent)
        self.checkboxBmp = JCheckBox("Process BMP images", actionPerformed=self.checkboxBmpEvent)
        self.checkboxGif = JCheckBox("Process GIF images", actionPerformed=self.checkboxGifEvent)
        
        self.preprocessing_label = JLabel("Image Preprocessing Options:")
        
        # Grayscale
        self.checkboxGrayscale = JCheckBox("Grayscale", actionPerformed=self.checkboxGrayscaleEvent)

        # Skip Resize Checkbox
        self.checkboxSkipResize = JCheckBox("Skip Resize", actionPerformed=self.checkboxSkipResizeEvent)
        
        # Resize radio buttons
        self.resizeLabel = JLabel("Resize:")
        self.resizeGroup = ButtonGroup()
        self.resize25 = JRadioButton("25%", actionPerformed=self.resizeEvent)
        self.resize50 = JRadioButton("50%", actionPerformed=self.resizeEvent)
        self.resize75 = JRadioButton("75%", actionPerformed=self.resizeEvent)
        self.resize100 = JRadioButton("100%", actionPerformed=self.resizeEvent)

        self.resizeGroup.add(self.resize25)
        self.resizeGroup.add(self.resize50)
        self.resizeGroup.add(self.resize75)
        self.resizeGroup.add(self.resize100)

        self.resizePanel = JPanel()
        self.resizePanel.setLayout(BoxLayout(self.resizePanel, BoxLayout.X_AXIS))
        self.resizePanel.add(self.resizeLabel)
        self.resizePanel.add(self.resize25)
        self.resizePanel.add(self.resize50)
        self.resizePanel.add(self.resize75)
        self.resizePanel.add(self.resize100)

        # ADDED: Language options
        self.language_label = JLabel("Language for Tesseract:")
        self.languageGroup = ButtonGroup()
        self.language_eng = JRadioButton("English (eng)", actionPerformed=self.languageEvent)
        self.language_srp = JRadioButton("Serbian (srp)", actionPerformed=self.languageEvent)
        self.language_deu = JRadioButton("German (deu)", actionPerformed=self.languageEvent)

        self.languageGroup.add(self.language_eng)
        self.languageGroup.add(self.language_srp)
        self.languageGroup.add(self.language_deu)
        
        self.languagePanel = JPanel()
        self.languagePanel.setLayout(BoxLayout(self.languagePanel, BoxLayout.X_AXIS))
        self.languagePanel.add(self.language_label)
        self.languagePanel.add(self.language_eng)
        self.languagePanel.add(self.language_srp)
        self.languagePanel.add(self.language_deu)

        self.add(self.image_types_label)
        self.add(self.checkboxJpg)
        self.add(self.checkboxPng)
        self.add(self.checkboxTif)
        self.add(self.checkboxBmp)
        self.add(self.checkboxGif)
        
        self.add(JPanel())
        
        self.add(self.preprocessing_label)
        self.add(self.checkboxGrayscale)
        self.add(self.checkboxSkipResize)
        self.add(self.resizePanel)
        
        self.add(JPanel())
        
        # ADDED: Add language panel to the UI
        self.add(self.languagePanel)

    def customizeComponents(self):
        self.checkboxJpg.setSelected(self.local_settings.getSetting("jpg_flag") == "true")
        self.checkboxPng.setSelected(self.local_settings.getSetting("png_flag") == "true")
        self.checkboxTif.setSelected(self.local_settings.getSetting("tif_flag") == "true")
        self.checkboxBmp.setSelected(self.local_settings.getSetting("bmp_flag") == "true")
        self.checkboxGif.setSelected(self.local_settings.getSetting("gif_flag") == "true")
        
        self.checkboxGrayscale.setSelected(self.local_settings.getSetting("grayscale_flag") == "true")

        skip_resize_flag = self.local_settings.getSetting("skip_resize_flag") == "true"
        self.checkboxSkipResize.setSelected(skip_resize_flag)
        
        # Set the enabled state of resize radio buttons
        self.resize25.setEnabled(not skip_resize_flag)
        self.resize50.setEnabled(not skip_resize_flag)
        self.resize75.setEnabled(not skip_resize_flag)
        self.resize100.setEnabled(not skip_resize_flag)

        # Set the selected resize radio button based on saved settings
        resize_value = self.local_settings.getSetting("resize_value")
        if not resize_value or skip_resize_flag:
            # Default to 100% if no value is saved or if skipping resize
            self.resize100.setSelected(True)
        elif resize_value == "25":
            self.resize25.setSelected(True)
        elif resize_value == "50":
            self.resize50.setSelected(True)
        elif resize_value == "75":
            self.resize75.setSelected(True)
        else:
            self.resize100.setSelected(True)
        
        # ADDED: Set the selected language radio button
        language_code = self.local_settings.getSetting("language_code")
        if not language_code:
            # Default to English
            self.language_eng.setSelected(True)
            self.local_settings.setSetting("language_code", "eng")
        elif language_code == "eng":
            self.language_eng.setSelected(True)
        elif language_code == "srp":
            self.language_srp.setSelected(True)
        elif language_code == "deu":
            self.language_deu.setSelected(True)

    def getSettings(self):
        return self.local_settings
