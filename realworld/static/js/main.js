/* My Blog main JavaScript */
document.addEventListener("DOMContentLoaded", function () {
    console.log("Blog loaded");
    document.querySelectorAll(".message").forEach(function (el) {
        setTimeout(function () {
            el.style.opacity = "0";
            setTimeout(function () { el.remove(); }, 300);
        }, 3000);
    });
});
