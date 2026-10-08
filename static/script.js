// Check that the phone number has exactly 10 digits

function validatePhone() {

    let phone = document.getElementById("phone").value;

    if (phone.length !== 10) {

        alert("Phone number must be exactly 10 digits.");

        return false;
    }

    if (!["6", "7", "8", "9"].includes(phone[0])) {

        alert("Phone number must start with 6, 7, 8 or 9.");

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


// Add a book to the cart without refreshing the page
function setupCartButtons() {

    let cartButtons =
        document.querySelectorAll(".add-to-cart-button");

    for (let button of cartButtons) {

        button.addEventListener("click", async function() {

            let bookId = button.dataset.bookId;

            let response = await fetch(
                "/cart/add/" + bookId,
                {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest"
                    }
                }
            );

            let result = await response.json();

            if (result.status === "added") {

                let cartCount =
                    document.querySelector(".cart-count");

                if (cartCount) {

                    cartCount.textContent = result.cart_count;
                }

                button.textContent = "Added";

                setTimeout(function() {

                    button.textContent = "Add to cart";

                }, 1000);

            } else {

                alert(result.message);
            }
        });
    }
}


// Add or remove a book from the wishlist
function setupWishlistButtons() {

    let wishlistButtons =
        document.querySelectorAll(".wishlist-button");

    for (let button of wishlistButtons) {

        button.addEventListener("click", async function() {

            let bookId = button.dataset.bookId;

            let response = await fetch(
                "/wishlist/add/" + bookId,
                {
                    method: "POST"
                }
            );

            let result = await response.json();

            if (result.status === "added") {

                button.classList.add("wishlist-active");

            } else {

                button.classList.remove("wishlist-active");
            }
        });
    }
}


// Search books from the server using fetch()
let searchBox = document.getElementById("searchBox");

if (searchBox) {

    searchBox.addEventListener("input", async function() {

        let query = searchBox.value;

        let response = await fetch(
            "/api/books?q=" + encodeURIComponent(query)
        );

        let books = await response.json();

        let bookGrid =
            document.getElementById("bookGrid");

        bookGrid.innerHTML = "";


        if (books.length === 0) {

            bookGrid.innerHTML = `
                <div class="no-books">
                    <h2>No books found</h2>
                    <p>Try a different search.</p>
                </div>
            `;

            return;
        }


        for (let book of books) {

            let bookCard =
                document.createElement("div");

            bookCard.className = "book-card";


            bookCard.innerHTML = `
                <a href="/book/${book.id}">

                    <img
                        src="/static/images/${book.image}"
                        alt="${book.title}"
                    >

                </a>

                <div class="book-card-content">

                    <span class="category-tag">
                        ${book.category}
                    </span>

                    <h2>
                        ${book.title}
                    </h2>

                    <p>
                        ${book.author}
                    </p>

                    <div class="book-bottom">

                        <strong class="book-price">
                            ₹${book.price}
                        </strong>

                        ${
                            book.stock > 0
                            ?
                            `<button
                                type="button"
                                class="add-button add-to-cart-button"
                                data-book-id="${book.id}"
                            >
                                Add to cart
                            </button>`
                            :
                            `<button
                                type="button"
                                class="add-button"
                                disabled
                            >
                                Out of stock
                            </button>`
                        }

                    </div>

                </div>
            `;


            bookGrid.appendChild(bookCard);
        }


        setupCartButtons();

    });
}


// Set up cart buttons when the page first loads
setupCartButtons();


// Set up wishlist buttons when the page first loads
setupWishlistButtons();

