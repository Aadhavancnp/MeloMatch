module.exports = {
  plugins: {
    'tailwindcss/nesting': {}, // Optional: if you want to use CSS nesting (plugin might be needed or part of TW v4)
    'tailwindcss': {},
    'autoprefixer': {},
    // For Tailwind v4, if it uses Lightning CSS, this might change or be simpler.
    // If Lightning CSS is built-in and handles prefixing, autoprefixer might not be needed.
    // For now, including it is a safe default based on common Tailwind setups.
  }
}
