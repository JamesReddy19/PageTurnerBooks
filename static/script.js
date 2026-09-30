function validatePhone() {

    let phone = document.getElementById("phone").value;

    if (phone.length !== 10) {
        alert("Phone number must be exactly 10 digits.");
        return false;
    }

    return true;
}