from pageturner import create_app

app = create_app()

if __name__ == "__main__":
    # Automatically reload the application when code changes during development.
    app.run(debug=True)

