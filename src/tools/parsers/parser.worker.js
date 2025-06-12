// Worker.js
// import MavlinkParser from 'mavlinkParser'
const mavparser = require('./mavlinkParser')
const DataflashParser = require('./JsDataflashParser/parser').default
const DjiParser = require('./djiParser').default

let parser
self.addEventListener('message', async function (event) {
    if (event.data === null) {
        console.log('got bad file message!')
    } else if (event.data.action === 'parse') {
        const data = event.data.file
        if (event.data.isTlog) {
            parser = new mavparser.MavlinkParser()
            const result = parser.processData(data)
            // Send available message types
            self.postMessage({
                availableMessages: result.types
            })
            // Send messages
            self.postMessage({
                messages: result.messages
            })
            // Send metadata
            self.postMessage({
                metadata: result.metadata
            })
            // Notify that messages are done loading
            self.postMessage({
                messagesDoneLoading: true
            })
        } else if (event.data.isDji) {
            parser = new DjiParser()
            const result = await parser.processData(data)
            // Send available message types
            self.postMessage({
                availableMessages: result.types
            })
            // Send messages
            self.postMessage({
                messages: result.messages
            })
            // Send metadata
            self.postMessage({
                metadata: result.metadata
            })
            // Notify that messages are done loading
            self.postMessage({
                messagesDoneLoading: true
            })
        } else {
            parser = new DataflashParser(true)
            const result = parser.processData(data, ['CMD', 'MSG', 'FILE', 'MODE', 'AHR2', 'ATT', 'GPS', 'POS',
                'XKQ1', 'XKQ', 'NKQ1', 'NKQ2', 'XKQ2', 'PARM', 'MSG', 'STAT', 'EV', 'XKF4', 'FNCE'])
            // Send available message types
            self.postMessage({
                availableMessages: result.types
            })
            // Send messages
            self.postMessage({
                messages: result.messages
            })
            // Send metadata
            self.postMessage({
                metadata: result.metadata
            })
            // Notify that messages are done loading
            self.postMessage({
                messagesDoneLoading: true
            })
        }
    } else if (event.data.action === 'loadType') {
        if (!parser) {
            console.log('parser not ready')
        }
        parser.loadType(event.data.type.split('[')[0])
    } else if (event.data.action === 'trimFile') {
        parser.trimFile(event.data.time)
    }
})
