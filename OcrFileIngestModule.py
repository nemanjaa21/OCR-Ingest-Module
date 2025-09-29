# -*- coding: utf-8 -*-

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
from javax.swing import JComboBox
from java.awt import Component
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

                processed_fd, processed_file_path = tempfile.mkstemp(suffix=".png")
                os.close(processed_fd)

                magick_args = []
                magick_args.append('magick')
                magick_args.append(temp_file_path)
                
                if self.local_settings.getSetting("grayscale_flag") == "true":
                    magick_args.append('-grayscale')
                    magick_args.append('Rec709Luminance')

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

                ocr_text = tesseract_stdout.decode('utf-8', 'ignore').strip()
                self.log(Level.INFO, "OCR text extracted from " + file.getName())

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
        self.resizeCombo.setEnabled(not skip_resize_flag)
        if not skip_resize_flag:
            current_resize_value = self.local_settings.getSetting("resize_value")
            if not current_resize_value:
                self.resizeCombo.setSelectedItem("100%")
                self.local_settings.setSetting("resize_value", "100")

    def resizeComboEvent(self, event):
        value = self.resizeCombo.getSelectedItem().replace('%', '')
        self.local_settings.setSetting("resize_value", value)

    def languageComboEvent(self, event):
        selected = self.languageCombo.getSelectedItem()
        if "eng" in selected:
            self.local_settings.setSetting("language_code", "eng")
        elif "srp" in selected:
            self.local_settings.setSetting("language_code", "srp")
        elif "deu" in selected:
            self.local_settings.setSetting("language_code", "deu")
        elif "fra" in selected:
            self.local_settings.setSetting("language_code", "fra")
        elif "spa" in selected:
            self.local_settings.setSetting("language_code", "spa")

    def initComponents(self):
        self.setLayout(BoxLayout(self, BoxLayout.Y_AXIS))

        self.image_types_label = JLabel("Image file formats:"); self.image_types_label.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.checkboxJpg = JCheckBox("Process JPG/JPEG images", actionPerformed=self.checkboxJpgEvent); self.checkboxJpg.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.checkboxPng = JCheckBox("Process PNG images", actionPerformed=self.checkboxPngEvent); self.checkboxPng.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.checkboxTif = JCheckBox("Process TIFF images", actionPerformed=self.checkboxTifEvent); self.checkboxTif.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.checkboxBmp = JCheckBox("Process BMP images", actionPerformed=self.checkboxBmpEvent); self.checkboxBmp.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.checkboxGif = JCheckBox("Process GIF images", actionPerformed=self.checkboxGifEvent); self.checkboxGif.setAlignmentX(Component.LEFT_ALIGNMENT)

        self.preprocessing_label = JLabel("Image Preprocessing Options:"); self.preprocessing_label.setAlignmentX(Component.LEFT_ALIGNMENT)

        self.checkboxGrayscale = JCheckBox("Grayscale", actionPerformed=self.checkboxGrayscaleEvent); self.checkboxGrayscale.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.checkboxSkipResize = JCheckBox("Skip Resize", actionPerformed=self.checkboxSkipResizeEvent); self.checkboxSkipResize.setAlignmentX(Component.LEFT_ALIGNMENT)

        self.resizeLabel = JLabel("Resize:"); self.resizeLabel.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.resizeCombo = JComboBox(["25%", "50%", "75%", "100%"]); self.resizeCombo.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.resizeCombo.addActionListener(self.resizeComboEvent)

        self.language_label = JLabel("Language for Tesseract:"); self.language_label.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.languageCombo = JComboBox(["English (eng)", "Serbian (srp)", "German (deu)", "French (fra)", "Spanish (spa)"]); self.languageCombo.setAlignmentX(Component.LEFT_ALIGNMENT)
        self.languageCombo.addActionListener(self.languageComboEvent)

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
        self.add(self.resizeLabel)
        self.add(self.resizeCombo)

        self.add(JPanel())

        self.add(self.language_label)
        self.add(self.languageCombo)

    def customizeComponents(self):
        self.checkboxJpg.setSelected(self.local_settings.getSetting("jpg_flag") == "true")
        self.checkboxPng.setSelected(self.local_settings.getSetting("png_flag") == "true")
        self.checkboxTif.setSelected(self.local_settings.getSetting("tif_flag") == "true")
        self.checkboxBmp.setSelected(self.local_settings.getSetting("bmp_flag") == "true")
        self.checkboxGif.setSelected(self.local_settings.getSetting("gif_flag") == "true")

        self.checkboxGrayscale.setSelected(self.local_settings.getSetting("grayscale_flag") == "true")

        skip_resize_flag = self.local_settings.getSetting("skip_resize_flag") == "true"
        self.checkboxSkipResize.setSelected(skip_resize_flag)
        self.resizeCombo.setEnabled(not skip_resize_flag)

        resize_value = self.local_settings.getSetting("resize_value")
        if resize_value in ["25", "50", "75", "100"]:
            self.resizeCombo.setSelectedItem(resize_value + "%")
        else:
            self.resizeCombo.setSelectedItem("100%")

        language_code = self.local_settings.getSetting("language_code")
        if language_code == "srp":
            self.languageCombo.setSelectedItem("Serbian (srp)")
        elif language_code == "deu":
            self.languageCombo.setSelectedItem("German (deu)")
        elif language_code == "fra":
            self.languageCombo.setSelectedItem("French (fra)")
        elif language_code == "spa":
            self.languageCombo.setSelectedItem("Spanish (spa)")
        else:
            self.languageCombo.setSelectedItem("English (eng)")
            self.local_settings.setSetting("language_code", "eng")

    def getSettings(self):
        return self.local_settings
