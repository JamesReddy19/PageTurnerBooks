//javascript
// Check that the phone number has exactly 10 digits
function validatePhone() {

    let phone = document.getElementById("phone").value;

    if (phone.length !== 10) {

        alert("Phone number must be exactly 10 digits.");

        return false;
    }

    return true;
}


// Hide the flash message after 3 seconds
let flashMessage = document.querySelector(".flash-message");

if (flashMessage) {

    setTimeout(function() {

        flashMessage.style.display = "none";

    }, 3000);
}


// Search books by title or author
let searchBox = document.getElementById("searchBox");

let books = document.querySelectorAll(".book-card");

if (searchBox) {

    searchBox.addEventListener("input", function() {

        let searchText = searchBox.value.toLowerCase();

        for (let book of books) {

            let bookText = book.innerText.toLowerCase();

            if (bookText.includes(searchText)) {

                book.style.display = "";

            } else {

                book.style.display = "none";
            }
        }
    });
}

