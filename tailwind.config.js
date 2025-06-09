/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './**/templates/**/*.html', // More generic to catch all app templates
    './**/forms.py', // If you use Django forms and want to style them with Tailwind via class attributes in Python
  ],
  theme: {
    extend: {
      // You can extend the default Tailwind theme here
      // For example, add custom colors, fonts, spacing, etc.
      colors: {
        'primary': '#0d6efd', // Example: Bootstrap primary blue
        'secondary': '#6c757d', // Example: Bootstrap secondary gray
        // Add your project-specific colors
      },
    },
  },
  plugins: [
    // require('@tailwindcss/forms'), // Uncomment if you want to use the official forms plugin
    // require('@tailwindcss/typography'), // Uncomment for prose styling
    // require('@tailwindcss/aspect-ratio'), // Uncomment for aspect ratio utilities
  ],
}
