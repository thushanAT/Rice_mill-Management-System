/**
 * Rice Mill Management System - Shared Input Validation & UI Security Module
 * 
 * Features:
 * - Real-time field validation (Names, Usernames, Email, Sri Lankan Phone, Password, Price, Quantity)
 * - Clear red border (.is-invalid) and red error messages (.field-error-message) directly below fields
 * - Immediate removal of error message and red border once user corrects the value
 * - Prevention of form submission until required validation succeeds
 * - Keeps existing valid field values intact without clearing them
 * - Show / Hide Password toggle on password inputs
 * - Real-time Password Strength meter (Weak, Medium, Strong)
 */
"use strict";

(function (window, document) {
    const NAME_REGEX = /^[A-Za-z]+(?:[ -][A-Za-z]+)*$/;
    const USERNAME_REGEX = /^[A-Za-z0-9_]{4,50}$/;
    const EMAIL_REGEX = /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/;

    // Sri Lankan phone number regexes
    const SL_PHONE_CLEAN_REGEX = /[\s\-\(\)]/g;
    const SL_LOCAL_MOBILE_REGEX = /^07[0-9]{8}$/;
    const SL_INTL_MOBILE_REGEX = /^\+947[0-9]{8}$/;
    const SL_LOCAL_GENERAL_REGEX = /^0[1-9][0-9]{8}$/;
    const SL_INTL_GENERAL_REGEX = /^\+94[1-9][0-9]{8}$/;

    // Password complexity rules
    const PWD_UPPERCASE = /[A-Z]/;
    const PWD_LOWERCASE = /[a-z]/;
    const PWD_NUMBER = /[0-9]/;
    const PWD_SPECIAL = /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?`~]/;

    // Professional Lucide-style outline SVG icons for show/hide password
    const EYE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="password-eye-svg" aria-hidden="true"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
    const EYE_OFF_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="password-eye-svg" aria-hidden="true"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" x2="22" y1="2" y2="22"/></svg>';

    const RiceValidator = {
        /**
         * Show field error with red border and message directly below the element.
         */
        showError: function (input, message) {
            if (!input) return;
            input.classList.add("is-invalid");
            input.setAttribute("aria-invalid", "true");

            const container = input.closest(".password-input-wrap") || input;
            const parent = container.parentElement;
            if (!parent) return;

            let errorEl = parent.querySelector(`.field-error-message[data-for="${input.id || input.name}"]`);
            if (!errorEl) {
                errorEl = document.createElement("div");
                errorEl.className = "field-error-message";
                errorEl.setAttribute("data-for", input.id || input.name || "");
                errorEl.setAttribute("role", "alert");
                errorEl.setAttribute("aria-live", "polite");

                // Insert after the input or password wrapper, or after the password strength meter if present
                const strengthContainer = parent.querySelector(".password-strength-container");
                if (strengthContainer && strengthContainer.previousElementSibling === container) {
                    strengthContainer.after(errorEl);
                } else {
                    container.after(errorEl);
                }
            }
            errorEl.textContent = message;
        },

        /**
         * Clear error state and message from a field.
         */
        clearError: function (input) {
            if (!input) return;
            input.classList.remove("is-invalid");
            input.removeAttribute("aria-invalid");

            const container = input.closest(".password-input-wrap") || input;
            const parent = container.parentElement;
            if (!parent) return;

            const errorEl = parent.querySelector(`.field-error-message[data-for="${input.id || input.name}"]`);
            if (errorEl) {
                errorEl.remove();
            }
        },

        /**
         * Clear all errors within a container or form.
         */
        clearAllErrors: function (container) {
            if (!container) return;
            container.querySelectorAll(".is-invalid").forEach(function (el) {
                el.classList.remove("is-invalid");
                el.removeAttribute("aria-invalid");
            });
            container.querySelectorAll(".field-error-message").forEach(function (el) {
                el.remove();
            });
        },

        /**
         * Validate person's name (First Name, Last Name).
         */
        validateName: function (value, label = "Name", required = true) {
            const v = String(value || "").trim();
            if (!v) {
                return required ? { valid : false, message: `${label} is required.` } : { valid: true };
            }
            if (!NAME_REGEX.test(v)) {
                return { valid: false, message: "Only alphabetic characters, spaces, and hyphens are allowed." };
            }
            if (v.length > 100) {
                return { valid: false, message: `${label} must be 100 characters or fewer.` };
            }
            return { valid: true };
        },

        /**
         * Validate username.
         */
        validateUsername: function (value, required = true) {
            const v = String(value || "").trim();
            if (!v) {
                return required ? { valid : false, message: "Username is required." } : { valid: true };
            }
            if (v.length < 4) {
                return { valid: false, message: "Username must contain at least 4 characters." };
            }
            if (v.length > 50) {
                return { valid: false, message: "Username must be 50 characters or fewer." };
            }
            if (!USERNAME_REGEX.test(v)) {
                return { valid: false, message: "Username can only contain letters, numbers, and underscores." };
            }
            return { valid: true };
        },

        /**
         * Validate email address format.
         */
        validateEmail: function (value, required = false) {
            const v = String(value || "").trim();
            if (!v) {
                return required ? { valid : false, message: "Email address is required." } : { valid: true };
            }
            if (v.length > 160) {
                return { valid: false, message: "Email address must be 160 characters or fewer." };
            }
            if (!EMAIL_REGEX.test(v)) {
                return { valid: false, message: "Please enter a valid email address." };
            }
            return { valid: true };
        },

        /**
         * Validate Sri Lankan phone number format.
         */
        validatePhone: function (value, required = false) {
            const raw = String(value || "").trim();
            if (!raw) {
                return required ? { valid : false, message: "Contact number is required." } : { valid: true };
            }
            if (raw.length > 30) {
                return { valid: false, message: "Contact number must be 30 characters or fewer." };
            }
            let cleaned = raw.replace(SL_PHONE_CLEAN_REGEX, "");
            if (cleaned.startsWith("0094")) cleaned = "+94" + cleaned.slice(4);
            else if (cleaned.startsWith("94") && cleaned.length === 11) cleaned = "+94" + cleaned.slice(2);

            const isLocal = cleaned.startsWith("0") && cleaned.length === 10 && /^\d+$/.test(cleaned);
            const isIntl = cleaned.startsWith("+94") && cleaned.length === 12 && /^\d+$/.test(cleaned.slice(1));

            if (isLocal) {
                if (SL_LOCAL_MOBILE_REGEX.test(cleaned) || SL_LOCAL_GENERAL_REGEX.test(cleaned)) {
                    return { valid: true, normalized: "+94" + cleaned.slice(1) };
                }
            } else if (isIntl) {
                if (SL_INTL_MOBILE_REGEX.test(cleaned) || SL_INTL_GENERAL_REGEX.test(cleaned)) {
                    return { valid: true, normalized: cleaned };
                }
            }
            return { valid: false, message: "Please enter a valid Sri Lankan phone number (e.g., 07XXXXXXXX or +947XXXXXXXX)." };
        },

        /**
         * Validate password complexity.
         */
        validatePassword: function (password, required = true) {
            const pwd = String(password || "");
            if (!pwd) {
                return required ? { valid : false, message: "Password is required." } : { valid: true };
            }
            if (pwd.length < 8) {
                return { valid: false, message: "Password must contain at least 8 characters." };
            }
            if (pwd.length > 128) {
                return { valid: false, message: "Password must be 128 characters or fewer." };
            }
            if (!PWD_UPPERCASE.test(pwd)) {
                return { valid: false, message: "Password must include at least one uppercase letter (A-Z)." };
            }
            if (!PWD_LOWERCASE.test(pwd)) {
                return { valid: false, message: "Password must include at least one lowercase letter (a-z)." };
            }
            if (!PWD_NUMBER.test(pwd)) {
                return { valid: false, message: "Password must include at least one number (0-9)." };
            }
            if (!PWD_SPECIAL.test(pwd)) {
                return { valid: false, message: "Password must include at least one special character (!@#$%^&*...)." };
            }
            return { valid: true };
        },

        /**
         * Validate confirm password matches password.
         */
        validateConfirmPassword: function (password, confirmPassword) {
            if (String(password || "") !== String(confirmPassword || "")) {
                return { valid: false, message: "Passwords do not match." };
            }
            return { valid: true };
        },

        /**
         * Validate positive price or decimal.
         */
        validatePrice: function (value, label = "Price", allowZero = false, required = true) {
            const v = String(value === null || value === undefined ? "" : value).trim();
            if (!v) {
                return required ? { valid : false, message: `${label} is required.` } : { valid: true };
            }
            const n = Number(v);
            if (isNaN(n) || !isFinite(n)) {
                return { valid: false, message: `${label} must be a valid number.` };
            }
            if (n < 0) {
                return { valid: false, message: `${label} must be a positive number.` };
            }
            if (!allowZero && n === 0) {
                return { valid: false, message: `${label} must be greater than zero.` };
            }
            const parts = v.split(".");
            if (parts.length > 1 && parts[1].length > 2) {
                return { valid: false, message: `${label} may have at most two decimal places.` };
            }
            return { valid: true, value: n };
        },

        /**
         * Validate positive quantity.
         */
        validateQuantity: function (value, label = "Quantity", allowZero = false, required = true) {
            const v = String(value === null || value === undefined ? "" : value).trim();
            if (!v) {
                return required ? { valid : false, message: `${label} is required.` } : { valid: true };
            }
            const n = Number(v);
            if (isNaN(n) || !isFinite(n)) {
                return { valid: false, message: `${label} must be a valid number.` };
            }
            if (n < 0) {
                return { valid: false, message: `${label} must be a positive number.` };
            }
            if (!allowZero && n === 0) {
                return { valid: false, message: `${label} must be greater than zero.` };
            }
            return { valid: true, value: n };
        },

        /**
         * Calculate password strength.
         */
        calculateStrength: function (pwd) {
            const p = String(pwd || "");
            if (!p) return { score: 0, status: "none", label: "" };
            const hasLen = p.length >= 8;
            const hasUpper = PWD_UPPERCASE.test(p);
            const hasLower = PWD_LOWERCASE.test(p);
            const hasNum = PWD_NUMBER.test(p);
            const hasSpec = PWD_SPECIAL.test(p);
            const count = [hasUpper, hasLower, hasNum, hasSpec].filter(Boolean).length;

            if (!hasLen || count < 2) {
                return { score: 1, status: "weak", label: "Weak" };
            }
            if (count < 4 || p.length < 10) {
                return { score: 2, status: "medium", label: "Medium" };
            }
            return { score: 3, status: "strong", label: "Strong" };
        },

        /**
         * Attach show/hide password toggle to an input element.
         */
        attachPasswordToggle: function (input) {
            if (!input || input.dataset.toggleAttached === "true") return;
            input.dataset.toggleAttached = "true";

            let wrapper = input.parentElement;
            if (!wrapper.classList.contains("password-input-wrap")) {
                wrapper = document.createElement("div");
                wrapper.className = "password-input-wrap";
                input.parentNode.insertBefore(wrapper, input);
                wrapper.appendChild(input);
            }

            const toggleBtn = document.createElement("button");
            toggleBtn.type = "button";
            toggleBtn.className = "password-toggle-btn";
            toggleBtn.setAttribute("aria-label", "Show password");
            toggleBtn.setAttribute("title", "Show password");
            toggleBtn.innerHTML = EYE_SVG;

            toggleBtn.addEventListener("click", function (e) {
                e.preventDefault();
                const isPassword = input.type === "password";

                // Save selection range and cursor position
                let start = null, end = null;
                try {
                    start = input.selectionStart;
                    end = input.selectionEnd;
                } catch (_) {}

                input.type = isPassword ? "text" : "password";
                toggleBtn.innerHTML = isPassword ? EYE_OFF_SVG : EYE_SVG;
                const newLabel = isPassword ? "Hide password" : "Show password";
                toggleBtn.setAttribute("aria-label", newLabel);
                toggleBtn.setAttribute("title", newLabel);

                // Preserve cursor position and restore focus to input
                input.focus();
                if (start !== null && end !== null) {
                    try {
                        input.setSelectionRange(start, end);
                    } catch (_) {}
                }
            });

            wrapper.appendChild(toggleBtn);
        },

        /**
         * Attach password strength meter to an input element.
         */
        attachPasswordStrengthMeter: function (input) {
            if (!input || input.dataset.strengthAttached === "true") return;

            // Never attach password strength checker on login forms or pages
            const form = input.form || input.closest("form");
            const action = (form?.getAttribute("action") || "").toLowerCase();
            const formId = (form?.id || "").toLowerCase();
            const isLogin = action.includes("login") || formId.includes("login") ||
                window.location.pathname.endsWith("/login") || window.location.pathname === "/";
            if (isLogin) return;

            input.dataset.strengthAttached = "true";

            const container = input.closest(".password-input-wrap") || input;
            const meter = document.createElement("div");
            meter.className = "password-strength-container";
            meter.innerHTML = `
                <div class="password-strength-bar">
                    <div class="password-strength-fill" role="progressbar" aria-valuemin="0" aria-valuemax="100"></div>
                </div>
                <div class="password-strength-label">
                    <span>Password Strength</span>
                    <span class="strength-text"></span>
                </div>
            `;
            container.after(meter);

            const fill = meter.querySelector(".password-strength-fill");
            const text = meter.querySelector(".strength-text");

            const updateMeter = function () {
                const strength = RiceValidator.calculateStrength(input.value);
                fill.className = "password-strength-fill";
                text.className = "strength-text";

                if (strength.status === "none") {
                    meter.style.display = "none";
                } else {
                    meter.style.display = "flex";
                    fill.classList.add(`strength-${strength.status}`);
                    text.classList.add(`strength-${strength.status}-text`);
                    text.textContent = strength.label;
                    fill.setAttribute("aria-valuenow", strength.score * 33);
                }
            };

            input.addEventListener("input", updateMeter);
            updateMeter();
        },

        /**
         * Validate an individual input element according to its attributes/types.
         */
        validateInput: function (input) {
            if (!input || input.disabled || input.readOnly || input.type === "hidden") return true;

            const name = (input.name || input.id || "").toLowerCase();
            const val = input.value;
            const required = input.hasAttribute("required") || input.required;
            let result = { valid: true };

            if (name.includes("firstname") || name.includes("first_name")) {
                result = RiceValidator.validateName(val, "First Name", required);
            } else if (name.includes("lastname") || name.includes("last_name")) {
                result = RiceValidator.validateName(val, "Last Name", required);
            } else if (name.includes("username")) {
                const form = input.form || input.closest("form");
                const isLoginForm = form && ((form.getAttribute("action") || "").includes("login") || form.id === "loginForm");
                if (isLoginForm) {
                    if (required && !String(val || "").trim()) {
                        result = { valid: false, message: "Username is required." };
                    }
                } else {
                    result = RiceValidator.validateUsername(val, required);
                }
            } else if (name.includes("email") || input.type === "email") {
                result = RiceValidator.validateEmail(val, required);
            } else if (name.includes("phone") || name.includes("contact")) {
                result = RiceValidator.validatePhone(val, required);
            } else if (name.includes("confirm") && (name.includes("password") || input.type === "password")) {
                // Find matching password field in same form
                const form = input.form || input.closest("form");
                const pwdInput = form ? form.querySelector('input[type="password"]:not([id*="onfirm"]):not([name*="onfirm"]):not([id*="urrent"]):not([name*="urrent"])') : null;
                if (pwdInput) {
                    result = RiceValidator.validateConfirmPassword(pwdInput.value, val);
                }
            } else if (input.type === "password" && !name.includes("current")) {
                const form = input.form || input.closest("form");
                const isLoginForm = form && ((form.getAttribute("action") || "").includes("login") || form.id === "loginForm");
                if (isLoginForm) {
                    if (required && !String(val || "").trim()) {
                        result = { valid: false, message: "Password is required." };
                    }
                } else {
                    // New / regular password
                    result = RiceValidator.validatePassword(val, required);
                }
            } else if (name.includes("creditlimit") || name.includes("credit_limit")) {
                result = RiceValidator.validatePrice(val, "Credit Limit", true, required);
            } else if (name.includes("price") || name.includes("rate") || name.includes("cost") || name.includes("amount")) {
                result = RiceValidator.validatePrice(val, "Price", false, required);
            } else if (name.includes("quantity") || name.includes("qty")) {
                result = RiceValidator.validateQuantity(val, "Quantity", false, required);
            } else if (required && !String(val || "").trim()) {
                const labelText = input.labels && input.labels[0] ? input.labels[0].textContent.replace("*", "").trim() : "This field";
                result = { valid: false, message: `${labelText} is required.` };
            }

            if (!result.valid) {
                RiceValidator.showError(input, result.message);
                return false;
            } else {
                RiceValidator.clearError(input);
                return true;
            }
        },

        /**
         * Attach real-time validation to a form.
         */
        initFormValidation: function (form) {
            if (!form || form.dataset.validationInitialized === "true") return;
            form.dataset.validationInitialized = "true";

            // Determine if this is a login form
            const action = (form.getAttribute("action") || "").toLowerCase();
            const formId = (form.id || "").toLowerCase();
            const isLoginForm = action.includes("login") || formId.includes("login") ||
                window.location.pathname.endsWith("/login") || window.location.pathname === "/";

            if (isLoginForm) {
                // Remove any strength container if one somehow exists on login
                form.querySelectorAll(".password-strength-container").forEach(function (el) {
                    el.remove();
                });
            }

            // Attach password toggles and strength meters
            form.querySelectorAll('input[type="password"]').forEach(function (pwdInput) {
                RiceValidator.attachPasswordToggle(pwdInput);
                const name = (pwdInput.name || pwdInput.id || "").toLowerCase();
                // Attach strength meter to new passwords (never on login)
                if (!isLoginForm && !name.includes("current") && !name.includes("confirm")) {
                    RiceValidator.attachPasswordStrengthMeter(pwdInput);
                }
            });

            // Real-time input listeners
            form.querySelectorAll("input, select, textarea").forEach(function (input) {
                if (input.type === "hidden" || input.type === "submit" || input.type === "button") return;

                input.addEventListener("input", function () {
                    // If field was invalid, re-validate immediately to clear error as user fixes it
                    if (input.classList.contains("is-invalid") || input.value.trim().length > 0) {
                        RiceValidator.validateInput(input);
                    }
                });

                input.addEventListener("blur", function () {
                    if (input.value.trim().length > 0 || input.required) {
                        RiceValidator.validateInput(input);
                    }
                });
            });

            // Intercept form submission
            form.addEventListener("submit", function (e) {
                let isValid = true;
                let firstInvalid = null;

                form.querySelectorAll("input, select, textarea").forEach(function (input) {
                    if (input.type === "hidden" || input.disabled || input.readOnly) return;
                    const fieldValid = RiceValidator.validateInput(input);
                    if (!fieldValid && isValid) {
                        isValid = false;
                        firstInvalid = input;
                    }
                });

                if (!isValid) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    if (firstInvalid) {
                        firstInvalid.focus();
                        if (firstInvalid.scrollIntoView) {
                            firstInvalid.scrollIntoView({ behavior: "smooth", block: "center" });
                        }
                    }
                }
            }, true); // Capture phase to ensure validation runs before custom submit listeners
        }
    };

    window.RiceValidator = RiceValidator;

    // Auto-initialize on DOM ready
    function autoInit() {
        // Enhance all forms on the page
        document.querySelectorAll("form").forEach(function (form) {
            RiceValidator.initFormValidation(form);
        });

        // Also standalone password inputs outside forms if any
        document.querySelectorAll('input[type="password"]:not([data-toggle-attached="true"])').forEach(function (pwd) {
            RiceValidator.attachPasswordToggle(pwd);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", autoInit, { once: true });
    } else {
        autoInit();
    }
})(window, document);
