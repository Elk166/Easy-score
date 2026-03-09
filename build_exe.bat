@echo off
pyinstaller ^
--onefile ^
--add-data "templates;templates" ^
--add-data "uploads;uploads" ^
--add-data "output;output" ^
--add-data "temp_images;temp_images" ^
--hidden-import=flask ^
--hidden-import=fitz ^
--hidden-import=oemer ^
--hidden-import=pdf2image ^
--hidden-import=PIL ^
--hidden-import=torch ^
--hidden-import=torchvision ^
--noconsole ^
--name="Sheet AI Score" ^
--icon="icon.ico" ^
--version-file="version_info.txt" ^
app.py
