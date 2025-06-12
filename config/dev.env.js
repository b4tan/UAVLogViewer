'use strict'
const { merge } = require('webpack-merge')
const prodEnv = require('./prod.env')
const fs = require('fs')
const path = require('path')

// Read token from file
const tokenPath = path.resolve(__dirname, '../VUE_APP_CESIUM_TOKEN.env')
let cesiumToken = ''
try {
    cesiumToken = fs.readFileSync(tokenPath, 'utf8').trim()
} catch (e) {
    console.warn('Could not read Cesium token file:', e)
}

module.exports = merge(prodEnv, {
  NODE_ENV: '"development"',
  VUE_APP_CESIUM_TOKEN: JSON.stringify(cesiumToken)
})
