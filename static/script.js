document.addEventListener("DOMContentLoaded", function () {
    // 1. Show book suggestions while typing.
    const searchInput = document.querySelector("#searchBox");
    const suggestionBox = document.querySelector("#search-suggestions");

    let searchTimer = null;
    let searchRequestNumber = 0;

    if (searchInput && suggestionBox) {
        searchInput.addEventListener("input", function () {
            clearTimeout(searchTimer);

            const query = searchInput.value.trim();
            const currentRequestNumber = ++searchRequestNumber;

            suggestionBox.replaceChildren();

            if (query.length < 2) {
                return;
            }

            searchTimer = setTimeout(async function () {
                try {
                    const response = await fetch(
                        "/api/books?q=" + encodeURIComponent(query)
                    );

                    if (!response.ok) {
                        return;
                    }

                    const data = await response.json();

                    if (
                        currentRequestNumber !== searchRequestNumber ||
                        !data ||
                        !Array.isArray(data.books)
                    ) {
                        return;
                    }

                    const list = document.createElement("ul");

                    data.books.slice(0, 5).forEach(function (book) {
                        const item = document.createElement("li");
                        const link = document.createElement("a");

                        link.href = "/book/" + encodeURIComponent(book.id);
                        link.textContent =
                            (book.title || "Untitled book") +
                            " — " +
                            (book.author || "Unknown author");

                        item.appendChild(link);
                        list.appendChild(item);
                    });

                    if (list.children.length > 0) {
                        suggestionBox.replaceChildren(list);
                    }
                } catch (error) {
                    // Suggestions are optional.
                }
            }, 250);
        });

        document.addEventListener("click", function (event) {
            if (
                !searchInput.contains(event.target) &&
                !suggestionBox.contains(event.target)
            ) {
                suggestionBox.replaceChildren();
            }
        });
    }

    // 2. Handle category checkboxes on pages that use them.
    const allCategoriesCheckbox =
        document.querySelector(".all-categories");

    const categoryCheckboxes = Array.from(
        document.querySelectorAll(
            ".category-pills input[type='checkbox']"
        )
    ).filter(function (checkbox) {
        return !checkbox.classList.contains("all-categories");
    });

    if (allCategoriesCheckbox) {
        allCategoriesCheckbox.addEventListener("change", function () {
            if (allCategoriesCheckbox.checked) {
                categoryCheckboxes.forEach(function (checkbox) {
                    checkbox.checked = false;
                });
            }
        });

        categoryCheckboxes.forEach(function (checkbox) {
            checkbox.addEventListener("change", function () {
                const anySelected = categoryCheckboxes.some(
                    function (checkbox) {
                        return checkbox.checked;
                    }
                );

                allCategoriesCheckbox.checked = !anySelected;
            });
        });

        const anySelected = categoryCheckboxes.some(
            function (checkbox) {
                return checkbox.checked;
            }
        );

        allCategoriesCheckbox.checked = !anySelected;
    }

    // 3. Read JSON responses safely.
    async function readJsonResponse(response) {
        try {
            return await response.json();
        } catch (error) {
            return {};
        }
    }

    // 4. Read the CSRF token required by the Flask application.
    function getCsrfToken() {
        const tokenElement = document.querySelector(
            'meta[name="csrf-token"]'
        );

        return tokenElement ? tokenElement.content : "";
    }

    // 5. Display messages without duplicating the message area.
    function showMessage(message, type) {
        let area = document.querySelector(".flash-list");

        if (!area) {
            area = document.createElement("div");
            area.className = "flash-list";

            const main = document.querySelector("main");

            if (main) {
                main.prepend(area);
            } else {
                document.body.prepend(area);
            }
        }

        const messageBox = document.createElement("div");
        messageBox.className = "flash " + type;
        messageBox.textContent = message;

        area.replaceChildren(messageBox);
    }

    // 6. Update the cart badge after a successful request.
    function updateCartBadge(count) {
        const badge = document.querySelector(".cart-badge");

        if (badge && count !== undefined) {
            badge.textContent = count;
        }
    }

    // 7. Add books to the cart from the homepage buttons.
    document.querySelectorAll(".add-to-cart-button").forEach(
        function (button) {
            button.addEventListener("click", async function () {
                const bookId = button.dataset.bookId;

                if (!bookId || button.disabled) {
                    return;
                }

                const originalText = button.textContent;

                button.disabled = true;
                button.textContent = "Adding…";

                try {
                    const response = await fetch(
                        "/cart/add/" + encodeURIComponent(bookId),
                        {
                            method: "POST",
                            headers: {
                                "X-Requested-With": "XMLHttpRequest",
                                "X-CSRF-Token": getCsrfToken(),
                                "Content-Type":
                                    "application/x-www-form-urlencoded"
                            },
                            body: new URLSearchParams({
                                quantity: "1"
                            })
                        }
                    );

                    const result = await readJsonResponse(response);

                    if (!response.ok || result.ok !== true) {
                        showMessage(
                            result.message ||
                                "Could not add the book to your cart.",
                            "error"
                        );
                        return;
                    }

                    updateCartBadge(result.count);

                    button.textContent = "Added ✓";

                    showMessage(
                        result.message || "Book added to your cart.",
                        "success"
                    );
                } catch (error) {
                    showMessage(
                        "Could not contact the server. Please try again.",
                        "error"
                    );
                } finally {
                    window.setTimeout(function () {
                        button.disabled = false;
                        button.textContent = originalText;
                    }, 700);
                }
            });
        }
    );

    // 8. Also support existing cart forms on other pages.
    document.querySelectorAll(
        'form[action*="/cart/add/"]'
    ).forEach(function (form) {
        form.addEventListener("submit", async function (event) {
            event.preventDefault();

            const button = form.querySelector(
                'button[type="submit"], button:not([type])'
            );

            const originalText = button ? button.textContent : "";

            if (button) {
                button.disabled = true;
                button.textContent = "Adding…";
            }

            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    body: new FormData(form),
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRF-Token": getCsrfToken()
                    }
                });

                const result = await readJsonResponse(response);

                if (!response.ok || result.ok !== true) {
                    showMessage(
                        result.message || "Could not add the book.",
                        "error"
                    );
                    return;
                }

                updateCartBadge(result.count);

                showMessage(
                    result.message || "Book added to your cart.",
                    "success"
                );
            } catch (error) {
                showMessage(
                    "Could not contact the server. Please try again.",
                    "error"
                );
            } finally {
                if (button) {
                    button.disabled = false;
                    button.textContent = originalText;
                }
            }
        });
    });

    // 9. Toggle wishlist hearts without reloading the page.
    document.querySelectorAll(
        'form[action*="/wishlist/toggle/"]'
    ).forEach(function (form) {
        form.addEventListener("submit", async function (event) {
            event.preventDefault();

            const button = form.querySelector(".wishlist-button");

            if (!button || button.disabled) {
                return;
            }

            const originalLabel = button.getAttribute("aria-label") || "";
            button.disabled = true;

            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    body: new FormData(form),
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRF-Token": getCsrfToken()
                    }
                });

                const result = await readJsonResponse(response);

                if (!response.ok || typeof result.added !== "boolean") {
                    showMessage(
                        result.message ||
                            (response.status === 401 ||
                            response.redirected
                                ? "Please log in to manage your wishlist."
                                : "Could not update the wishlist."),
                        "error"
                    );
                    return;
                }

                button.classList.toggle(
                    "wishlist-active",
                    result.added
                );

                button.classList.toggle("is-active", result.added);

                button.setAttribute(
                    "aria-pressed",
                    String(result.added)
                );

                button.setAttribute(
                    "title",
                    result.added
                        ? "Remove from wishlist"
                        : "Add to wishlist"
                );

                const heart = button.querySelector(
                    "[data-wishlist-heart]"
                );

                if (heart) {
                    heart.textContent = result.added ? "♥" : "♡";
                } else {
                    button.textContent = result.added ? "♥" : "♡";
                }

                button.setAttribute(
                    "aria-label",
                    result.added
                        ? "Remove from wishlist"
                        : "Add to wishlist"
                );

                showMessage(
                    result.message ||
                        (result.added
                            ? "Added to wishlist!"
                            : "Removed from wishlist."),
                    "success"
                );
            } catch (error) {
                button.setAttribute("aria-label", originalLabel);

                showMessage(
                    "Could not contact the server. Please try again.",
                    "error"
                );
            } finally {
                button.disabled = false;
            }
        });
    });

    // 10. Confirm forms before submitting.
    document.querySelectorAll("form[data-confirm]").forEach(
        function (form) {
            form.addEventListener("submit", function (event) {
                const message = form.dataset.confirm;

                if (message && !window.confirm(message)) {
                    event.preventDefault();
                }
            });
        }
    );

    // 11. Validate phone numbers on forms containing a phone field.
    document.querySelectorAll("input[name='phone']").forEach(
        function (phoneInput) {
            phoneInput.addEventListener("input", function () {
                phoneInput.value = phoneInput.value
                    .replace(/\D/g, "")
                    .slice(0, 10);
            });

            const form = phoneInput.closest("form");

            if (form) {
                form.addEventListener("submit", function (event) {
                    const phone = phoneInput.value.trim();

                    if (!/^\d{10}$/.test(phone)) {
                        event.preventDefault();

                        window.alert(
                            "Please enter a valid 10-digit phone number."
                        );

                        phoneInput.focus();
                    }
                });
            }
        }
    );

    // 12. Draw the revenue chart on the admin dashboard.
    const revenueCanvas = document.querySelector("#revenue-chart");

    if (revenueCanvas) {
        function drawRevenueChart() {
            const context = revenueCanvas.getContext("2d");

            if (!context) {
                return;
            }

            const values = (
                revenueCanvas.dataset.values || ""
            )
                .split(",")
                .map(function (value) {
                    return Number(value.trim());
                });

            const labels = (
                revenueCanvas.dataset.labels || ""
            )
                .split(",")
                .map(function (label) {
                    return label.trim();
                });

            const validData = values.every(function (value) {
                return Number.isFinite(value) && value >= 0;
            });

            const cssWidth = Math.max(
                280,
                Math.min(
                    revenueCanvas.parentElement.clientWidth || 600,
                    760
                )
            );

            const cssHeight = 300;
            const pixelRatio = window.devicePixelRatio || 1;

            revenueCanvas.style.display = "block";
            revenueCanvas.style.width = "100%";
            revenueCanvas.style.maxWidth = cssWidth + "px";
            revenueCanvas.style.height = cssHeight + "px";

            revenueCanvas.width = Math.round(cssWidth * pixelRatio);
            revenueCanvas.height = Math.round(cssHeight * pixelRatio);

            context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
            context.clearRect(0, 0, cssWidth, cssHeight);

            if (
                values.length === 0 ||
                !validData ||
                values.every(function (value) {
                    return value === 0;
                })
            ) {
                context.fillStyle = "#78847b";
                context.font = "12px sans-serif";
                context.textAlign = "center";
                context.textBaseline = "middle";
                context.fillText(
                    "No revenue recorded for this period",
                    cssWidth / 2,
                    cssHeight / 2
                );
                return;
            }

            const left = 70;
            const right = 18;
            const top = 24;
            const bottom = 52;
            const plotWidth = cssWidth - left - right;
            const plotHeight = cssHeight - top - bottom;

            const maxValue = Math.max(...values, 1);
            const step = Math.pow(
                10,
                Math.floor(Math.log10(maxValue))
            );
            const axisMax = Math.ceil(maxValue / step) * step;

            context.textBaseline = "middle";
            context.fillStyle = "#25352e";
            context.font = "bold 13px sans-serif";
            context.textAlign = "left";
            context.fillText("Revenue (₹)", left, 10);

            context.font = "11px sans-serif";
            context.textAlign = "right";

            for (let i = 0; i <= 4; i++) {
                const value = (axisMax / 4) * i;
                const y = top + plotHeight - (i / 4) * plotHeight;

                context.strokeStyle = "#e5e9e1";
                context.lineWidth = 1;
                context.beginPath();
                context.moveTo(left, y);
                context.lineTo(cssWidth - right, y);
                context.stroke();

                context.fillStyle = "#78847b";
                context.fillText(
                    "₹" + Math.round(value).toLocaleString("en-IN"),
                    left - 9,
                    y
                );
            }

            const slotWidth = plotWidth / values.length;
            const barWidth = Math.min(42, slotWidth * 0.55);

            values.forEach(function (value, index) {
                const barHeight = (value / axisMax) * plotHeight;
                const x =
                    left +
                    index * slotWidth +
                    (slotWidth - barWidth) / 2;
                const y = top + plotHeight - barHeight;

                context.fillStyle = "#28745c";
                context.beginPath();

                if (typeof context.roundRect === "function") {
                    context.roundRect(
                        x,
                        y,
                        barWidth,
                        Math.max(0, barHeight),
                        [5, 5, 0, 0]
                    );
                } else {
                    context.rect(
                        x,
                        y,
                        barWidth,
                        Math.max(0, barHeight)
                    );
                }

                context.fill();

                context.fillStyle = "#25352e";
                context.font = "11px sans-serif";
                context.textAlign = "center";

                context.fillText(
                    "₹" + Math.round(value).toLocaleString("en-IN"),
                    x + barWidth / 2,
                    Math.max(top + 8, y - 10)
                );

                const label = labels[index] || "Day " + (index + 1);

                context.fillStyle = "#59675d";
                context.font = "11px sans-serif";

                context.save();
                context.translate(
                    left + index * slotWidth + slotWidth / 2,
                    top + plotHeight + 18
                );

                if (label.length > 8) {
                    context.rotate(-Math.PI / 5);
                }

                context.fillText(label, 0, 0);
                context.restore();
            });
        }

        drawRevenueChart();

        let resizeTimer = null;

        window.addEventListener("resize", function () {
            clearTimeout(resizeTimer);

            resizeTimer = setTimeout(function () {
                drawRevenueChart();
            }, 150);
        });
    }

    // 12b. Draw the category sales chart on the admin dashboard.
    const categoryCanvas = document.querySelector("#category-chart");

    if (categoryCanvas) {
        function drawCategoryChart() {
            const context = categoryCanvas.getContext("2d");

            if (!context) {
                return;
            }

            const values = (categoryCanvas.dataset.values || "")
                .split(",")
                .map(function (value) {
                    return Number(value.trim());
                });

            const labels = (categoryCanvas.dataset.labels || "")
                .split(",")
                .map(function (label) {
                    return label.trim();
                });

            const validData = values.every(function (value) {
                return Number.isFinite(value) && value >= 0;
            });

            const cssWidth = Math.max(
                280,
                Math.min(categoryCanvas.parentElement.clientWidth || 600, 760)
            );

            const cssHeight = 260;
            const pixelRatio = window.devicePixelRatio || 1;

            categoryCanvas.style.display = "block";
            categoryCanvas.style.width = "100%";
            categoryCanvas.style.maxWidth = cssWidth + "px";
            categoryCanvas.style.height = cssHeight + "px";

            categoryCanvas.width = Math.round(cssWidth * pixelRatio);
            categoryCanvas.height = Math.round(cssHeight * pixelRatio);

            context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
            context.clearRect(0, 0, cssWidth, cssHeight);

            if (
                values.length === 0 ||
                !validData ||
                values.every(function (value) {
                    return value === 0;
                })
            ) {
                context.fillStyle = "#78847b";
                context.font = "12px sans-serif";
                context.textAlign = "center";
                context.textBaseline = "middle";
                context.fillText(
                    "No category sales recorded yet",
                    cssWidth / 2,
                    cssHeight / 2
                );
                return;
            }

            const left = 96;
            const right = 44;
            const top = 18;
            const bottom = 26;
            const plotWidth = cssWidth - left - right;
            const plotHeight = cssHeight - top - bottom;

            const maxValue = Math.max(...values, 1);
            const rowHeight = plotHeight / values.length;
            const barHeight = Math.min(24, rowHeight * 0.6);

            context.textBaseline = "middle";

            values.forEach(function (value, index) {
                const y =
                    top + index * rowHeight + (rowHeight - barHeight) / 2;
                const barWidth = (value / maxValue) * plotWidth;

                context.fillStyle = "#28745c";
                context.beginPath();

                if (typeof context.roundRect === "function") {
                    context.roundRect(
                        left,
                        y,
                        Math.max(0, barWidth),
                        barHeight,
                        [0, 5, 5, 0]
                    );
                } else {
                    context.rect(left, y, Math.max(0, barWidth), barHeight);
                }

                context.fill();

                context.fillStyle = "#25352e";
                context.font = "11px sans-serif";
                context.textAlign = "left";

                const label = labels[index] || "Category " + (index + 1);

                context.save();
                context.textAlign = "right";
                context.fillText(label, left - 10, y + barHeight / 2);
                context.restore();

                context.fillStyle = "#59675d";
                context.textAlign = "left";
                context.fillText(
                    value.toLocaleString("en-IN") + " copies",
                    left + Math.max(0, barWidth) + 8,
                    y + barHeight / 2
                );
            });
        }

        drawCategoryChart();

        let categoryResizeTimer = null;

        window.addEventListener("resize", function () {
            clearTimeout(categoryResizeTimer);

            categoryResizeTimer = setTimeout(function () {
                drawCategoryChart();
            }, 150);
        });
    }

    // 13. Compare books selected across different catalog pages.
    const compareForm = document.querySelector("#compareForm");
    const compareStorageKey = "pageturner_compare_book_ids";

    if (compareForm) {
        const compareCheckboxes = Array.from(
            document.querySelectorAll(
                'input[name="book_ids"][form="compareForm"]'
            )
        );

        // Read the book IDs selected on previous catalog pages.
        function getSavedBookIds() {
            try {
                const saved = JSON.parse(
                    sessionStorage.getItem(compareStorageKey) || "[]"
                );

                if (!Array.isArray(saved)) {
                    return [];
                }

                return Array.from(
                    new Set(
                        saved
                            .map(function (id) {
                                return String(id);
                            })
                            .filter(function (id) {
                                return /^\d+$/.test(id);
                            })
                    )
                ).slice(0, 2);
            } catch (error) {
                return [];
            }
        }

        let selectedBookIds = getSavedBookIds();

        // Save selections so they survive moving between catalog pages.
        function saveBookIds() {
            try {
                sessionStorage.setItem(
                    compareStorageKey,
                    JSON.stringify(selectedBookIds)
                );
            } catch (error) {
                showMessage(
                    "Your browser could not save the comparison selection.",
                    "error"
                );
            }
        }

        // Show the saved selection on whichever catalog page is open.
        function updateCompareCheckboxes() {
            compareCheckboxes.forEach(function (checkbox) {
                checkbox.checked = selectedBookIds.includes(
                    String(checkbox.value)
                );
            });

            let countMessage = document.querySelector(
                "#compare-selection-count"
            );

            if (!countMessage) {
                countMessage = document.createElement("p");
                countMessage.id = "compare-selection-count";
                compareForm.prepend(countMessage);
            }

            countMessage.textContent =
                "Books selected for comparison: " +
                selectedBookIds.length +
                " of 2";
        }

        updateCompareCheckboxes();

        // Add or remove a selected book.
        compareCheckboxes.forEach(function (checkbox) {
            checkbox.addEventListener("change", function () {
                const bookId = String(checkbox.value);

                if (checkbox.checked) {
                    if (selectedBookIds.includes(bookId)) {
                        return;
                    }

                    if (selectedBookIds.length >= 2) {
                        checkbox.checked = false;

                        showMessage(
                            "You can compare only two books. Unselect a book first.",
                            "error"
                        );

                        return;
                    }

                    selectedBookIds.push(bookId);
                } else {
                    selectedBookIds = selectedBookIds.filter(
                        function (id) {
                            return id !== bookId;
                        }
                    );
                }

                saveBookIds();
                updateCompareCheckboxes();
            });
        });

        // Submit both saved IDs, including books selected on other pages.
        compareForm.addEventListener("submit", function (event) {
            event.preventDefault();

            if (selectedBookIds.length !== 2) {
                showMessage(
                    "Please select exactly two different books. Your selection is kept when you change pages.",
                    "error"
                );

                return;
            }

            const compareUrl = new URL(
                compareForm.action,
                window.location.origin
            );

            compareUrl.search = "";

            selectedBookIds.forEach(function (bookId) {
                compareUrl.searchParams.append("book_ids", bookId);
            });

            window.location.href = compareUrl.toString();
        });
    }
});
