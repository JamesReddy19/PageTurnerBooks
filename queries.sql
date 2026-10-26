-- 1. Books priced below ₹500 (price is stored in paise).
SELECT id, title, author, price_paise FROM books WHERE price_paise < 50000 ORDER BY price_paise;

-- 2. Technology books, highest price first.
SELECT b.title, b.author, b.price_paise FROM books b JOIN book_categories bc ON bc.book_id=b.id JOIN categories c ON c.id=bc.category_id WHERE c.name='Technology' ORDER BY b.price_paise DESC;

-- 3. Count books in each category.
SELECT c.name, COUNT(DISTINCT bc.book_id) AS book_count FROM categories c LEFT JOIN book_categories bc ON bc.category_id=c.id GROUP BY c.id ORDER BY c.name;

-- 4. Most expensive book.
SELECT title, author, price_paise FROM books WHERE price_paise=(SELECT MAX(price_paise) FROM books);

-- 5. Every order with customer name and total, including orders without items.
SELECT o.id, o.name, o.total_paise, o.status FROM orders o ORDER BY o.id DESC;

-- 6. Number of orders and revenue, excluding cancelled orders.
SELECT COUNT(*) AS order_count, COALESCE(SUM(total_paise),0) AS revenue_paise FROM orders WHERE status!='Cancelled';

-- 7. Best-selling books.
SELECT b.title, SUM(oi.quantity) AS copies_sold FROM order_items oi JOIN books b ON b.id=oi.book_id JOIN orders o ON o.id=oi.order_id WHERE o.status!='Cancelled' GROUP BY b.id ORDER BY copies_sold DESC LIMIT 5;

-- 8. Order count per category.
SELECT c.name, COUNT(DISTINCT o.id) AS order_count FROM categories c JOIN book_categories bc ON bc.category_id=c.id JOIN order_items oi ON oi.book_id=bc.book_id JOIN orders o ON o.id=oi.order_id GROUP BY c.id ORDER BY order_count DESC;

-- 9. Customers who bought the same books in an order also bought these books.
SELECT other.title, COUNT(*) AS together_count FROM order_items chosen JOIN order_items other_item ON other_item.order_id=chosen.order_id AND other_item.book_id<>chosen.book_id JOIN books other ON other.id=other_item.book_id WHERE chosen.book_id=? GROUP BY other.id ORDER BY together_count DESC LIMIT 5;

-- 10. Average review rating per book.
SELECT b.title, ROUND(AVG(r.rating),1) AS average_rating, COUNT(r.id) AS review_count FROM books b LEFT JOIN reviews r ON r.book_id=b.id GROUP BY b.id;

-- 11. Daily revenue in a date range. Supply start and end dates.
SELECT date(date) AS order_day, COUNT(*) AS order_count, SUM(total_paise) AS revenue_paise FROM orders WHERE date(date)>=? AND date(date)<=? AND status!='Cancelled' GROUP BY date(date) ORDER BY order_day;

-- 12. Inspect whether the category index can help this lookup.
EXPLAIN QUERY PLAN SELECT id,title,price_paise FROM books WHERE category=? ORDER BY price_paise;
