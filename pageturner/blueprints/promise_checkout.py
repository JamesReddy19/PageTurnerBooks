
from flask import Blueprint, render_template

promise_checkout_bp = Blueprint("promise_checkout", __name__)


@promise_checkout_bp.get("/promise-checkout-demo")
def promise_checkout_demo():
    return render_template("promise_checkout.html")
