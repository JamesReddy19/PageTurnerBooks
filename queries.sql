-- 1. Find all books cheaper than 500
SELECT *
FROM books
WHERE price < 500;


-- 2. Find Technology books from highest price to lowest price
SELECT *
FROM books
WHERE category = 'Technology'
ORDER BY price DESC;


-- 3. Count how many books are in each category
SELECT category, COUNT(*) AS book_count
FROM books
GROUP BY category;


-- 4. Find the most expensive book
SELECT *
FROM books
ORDER BY price DESC
LIMIT 1;


-- 5. Show each order with customer name and total
SELECT orders.id, orders.name, orders.total
FROM orders
JOIN order_items
ON orders.id = order_items.order_id
GROUP BY orders.id;

-- 6. Total orders and total revenue
SELECT
    COUNT(*) AS total_orders,
    SUM(total) AS total_revenue
FROM orders;


-- 7. Best-selling books
SELECT
    books.title,
    SUM(order_items.quantity) AS total_sold
FROM order_items
JOIN books
    ON order_items.book_id = books.id
GROUP BY books.id, books.title
ORDER BY total_sold DESC;


-- 8. Number of orders per category
SELECT
    books.category,
    COUNT(DISTINCT order_items.order_id) AS order_count
FROM order_items
JOIN books
    ON order_items.book_id = books.id
GROUP BY books.category
ORDER BY order_count DESC;