/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // LeadX "Ink & Brass": deep navy ink identity, brass for opportunity signals.
        brand: {
          50: "#F1F4FA", 100: "#E2E8F4", 200: "#C4D1E8", 300: "#98AED5",
          400: "#6787BE", 500: "#4064A3", 600: "#2C4C86", 700: "#203B6A",
          800: "#182D51", 900: "#11213C",
        },
        // Brass accent (opportunity / attention). Kept under the legacy `signal` name.
        signal: {
          50: "#FBF7EE", 100: "#F5EBD3", 200: "#EAD4A2", 300: "#DBB86F",
          400: "#C99D48", 500: "#AC8030", 600: "#8D6726", 700: "#6F501F",
          800: "#533C19", 900: "#3A2A12",
        },
        accent: {
          50: "#FBF7EE", 100: "#F5EBD3", 200: "#EAD4A2", 300: "#DBB86F",
          400: "#C99D48", 500: "#AC8030", 600: "#8D6726", 700: "#6F501F",
          800: "#533C19", 900: "#3A2A12",
        },
        // Danger scale (legacy name).
        rust: {
          50: "#FDF2F1", 100: "#FADFDC", 200: "#F3BDB7", 300: "#E8958C",
          400: "#D96C61", 500: "#C24E42", 600: "#A63C31", 700: "#863129",
          800: "#662722", 900: "#461C18",
        },
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          2: "rgb(var(--surface-2) / <alpha-value>)",
          3: "rgb(var(--surface-3) / <alpha-value>)",
        },
        ink: {
          DEFAULT: "rgb(var(--ink) / <alpha-value>)",
          muted: "rgb(var(--ink-muted) / <alpha-value>)",
          faint: "rgb(var(--ink-faint) / <alpha-value>)",
        },
        line: {
          DEFAULT: "rgb(var(--line) / <alpha-value>)",
          strong: "rgb(var(--line-strong) / <alpha-value>)",
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"],
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      boxShadow: {
        card: "0 1px 2px rgb(var(--shadow-color) / .05), 0 1px 1px rgb(var(--shadow-color) / .03)",
        lift: "0 12px 32px -8px rgb(var(--shadow-color) / .18), 0 2px 6px rgb(var(--shadow-color) / .06)",
        glow: "0 0 0 1px rgb(var(--brand) / .18), 0 8px 24px rgb(var(--brand) / .12)",
      },
      borderRadius: {
        xl2: "0.625rem",
      },
    },
  },
  plugins: [],
};
