# Understanding the PageTurner Books files

`app.py` is intentionally small. It starts the application by calling `create_app()` from the `pageturner` package. The route logic and database work live in the files under `pageturner/`.

The HTML pages are in the top-level `templates/` folder. CSS and JavaScript are in the top-level `static/` folder. The application factory explicitly points Flask to those two folders so the pages can be found when the application is launched from this project.

Keep the original `PageTurnerBooks` folder and its database until you have run the new app and checked the imported data. The test suite uses temporary databases; it should not be used as a reason to delete the original project.
