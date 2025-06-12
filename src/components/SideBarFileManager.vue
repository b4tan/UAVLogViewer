<template>
    <div>
        <li  v-if="file==null && !sampleLoaded" >
            <a @click="onLoadSample('sample')" class="section"><i class="fas fa-play"></i>  Open Sample </a>
        </li>
        <li v-if="url">
            <a @click="share" class="section"><i class="fas fa-share-alt"></i> {{ shared ? 'Copied to clipboard!' :
                'Share link'}}</a>
        </li>
        <li v-if="url">
            <a :href="'/uploaded/' + url" class="section" target="_blank"><i class="fas fa-download"></i> Download</a>
        </li>
        <div @click="browse" @dragover.prevent @drop="onDrop" id="drop_zone"
        v-if="file==null && uploadpercentage===-1  && !sampleLoaded">
            <p>Drop *.tlog or *.bin file here or click to browse</p>
            <input @change="onChange" id="choosefile" style="opacity: 0;" type="file">
        </div>
        <!--<b-form-checkbox @change="uploadFile()" class="uploadCheckbox" v-if="file!=null && !uploadStarted"> Upload
        </b-form-checkbox>-->
        <VProgress v-bind:complete="transferMessage"
                   v-bind:percent="uploadpercentage"
                   v-if="uploadpercentage > -1">
        </VProgress>
        <VProgress v-bind:complete="state.processStatus"
                   v-bind:percent="state.processPercentage"
                   v-if="state.processPercentage > -1"
        ></VProgress>
    </div>
</template>
<script>
import VProgress from './SideBarFileManagerProgressBar.vue'
import Worker from '../tools/parsers/parser.worker.js'
import { store } from './Globals'

import { MAVLink20Processor as MAVLink } from '../libs/mavlink'

const worker = new Worker()

worker.addEventListener('message', function (event) {
})

export default {
    name: 'Dropzone',
    data: function () {
        return {
            // eslint-disable-next-line no-undef
            mavlinkParser: new MAVLink(),
            uploadpercentage: -1,
            sampleLoaded: false,
            shared: false,
            url: null,
            transferMessage: '',
            state: store,
            file: null,
            uploadStarted: false
        }
    },
    created () {
        this.$eventHub.$on('loadType', this.loadType)
        this.$eventHub.$on('trimFile', this.trimFile)
    },
    beforeDestroy () {
        this.$eventHub.$off('open-sample')
        this.$eventHub.$off('loadType')
        this.$eventHub.$off('trimFile')
        this.clearFlightData()
    },
    methods: {
        trimFile () {
            worker.postMessage({ action: 'trimFile', time: this.state.timeRange })
        },
        async onLoadSample (file) {
            if (file === 'sample') {
                this.state.file = 'sample'
                this.state.logType = 'tlog'
                try {
                    // First process the sample file for visualization
                    const sampleUrl = require('../assets/vtol.tlog').default
                    const oReq = new XMLHttpRequest()
                    oReq.open('GET', sampleUrl, true)
                    oReq.responseType = 'arraybuffer'
                    oReq.onload = async (oEvent) => {
                        const arrayBuffer = oReq.response
                        this.transferMessage = 'Download Done'
                        // Process for visualization
                        worker.postMessage({
                            action: 'parse',
                            file: arrayBuffer,
                            isTlog: true,
                            isDji: false
                        })

                        // Call the backend to process the sample file
                        const response = await fetch('http://localhost:8000/api/open-sample', {
                            method: 'POST'
                        })
                        if (!response.ok) {
                            throw new Error('Failed to process sample file')
                        }
                        const { fileKey } = await response.json()
                        if (fileKey) {
                            this.state.dataLoaded = true
                            this.state.currentFileKey = fileKey
                            this.$eventHub.$emit('file-loaded', fileKey)
                            console.log('Sample file processed successfully:', fileKey)
                        } else {
                            throw new Error('No file key returned from server')
                        }
                    }
                    oReq.addEventListener('progress', (e) => {
                        if (e.lengthComputable) {
                            this.uploadpercentage = 100 * e.loaded / e.total
                        }
                    }, false)
                    oReq.onerror = (error) => {
                        console.error('Error loading sample file:', error)
                        this.clearFlightData()
                    }
                    oReq.send()
                } catch (error) {
                    console.error('Error processing sample file:', error)
                    this.clearFlightData()
                }
            } else {
                const fileUrl = file
                // Set the file name for display purposes
                const urlParts = fileUrl.split('/')
                this.state.file = urlParts[urlParts.length - 1]
                const oReq = new XMLHttpRequest()
                console.log(`loading file from ${fileUrl}`)
                // Set the log type based on file extension
                this.state.logType = fileUrl.indexOf('.tlog') > 0 ? 'tlog' : 'bin'
                if (fileUrl.indexOf('.txt') > 0) {
                    this.state.logType = 'dji'
                }
                oReq.open('GET', fileUrl, true)
                oReq.responseType = 'arraybuffer'
                oReq.onload = async (oEvent) => {
                    const arrayBuffer = oReq.response
                    this.transferMessage = 'Download Done'
                    worker.postMessage({
                        action: 'parse',
                        file: arrayBuffer,
                        isTlog: (fileUrl.indexOf('.tlog') > 0),
                        isDji: (fileUrl.indexOf('.txt') > 0)
                    })

                    // Send file to backend
                    try {
                        const formData = new FormData()
                        formData.append(
                            'file',
                            new Blob([arrayBuffer], { type: 'application/octet-stream' }),
                            this.state.file
                        )
                        const response = await fetch('http://localhost:8000/api/upload', {
                            method: 'POST',
                            body: formData
                        })
                        if (!response.ok) {
                            throw new Error('Failed to upload file')
                        }
                        const { fileKey } = await response.json()
                        if (fileKey) {
                            this.state.dataLoaded = true
                            this.state.currentFileKey = fileKey
                            this.$eventHub.$emit('file-loaded', fileKey)
                            console.log('File processed successfully:', fileKey)
                        } else {
                            throw new Error('No file key returned from server')
                        }
                    } catch (error) {
                        console.error('Error uploading file:', error)
                        this.clearFlightData()
                    }
                }
                oReq.addEventListener('progress', (e) => {
                    if (e.lengthComputable) {
                        this.uploadpercentage = 100 * e.loaded / e.total
                    }
                }, false)
                oReq.onerror = (error) => {
                    alert('unable to fetch remote file, check CORS settings in the target server')
                    console.log(error)
                    this.state.dataLoaded = false
                    this.state.currentFileKey = null
                }
                oReq.send()
            }
        },
        onChange (ev) {
            const fileinput = document.getElementById('choosefile')
            this.process(fileinput.files[0])
        },
        onDrop (ev) {
            // Prevent default behavior (Prevent file from being opened)
            ev.preventDefault()
            if (ev.dataTransfer.items) {
                // Use DataTransferItemList interface to access the file(s)
                for (let i = 0; i < ev.dataTransfer.items.length; i++) {
                    // If dropped items aren't files, reject them
                    if (ev.dataTransfer.items[i].kind === 'file') {
                        const file = ev.dataTransfer.items[i].getAsFile()
                        this.process(file)
                    }
                }
            } else {
                // Use DataTransfer interface to access the file(s)
                for (let i = 0; i < ev.dataTransfer.files.length; i++) {
                    console.log('... file[' + i + '].name = ' + ev.dataTransfer.files[i].name)
                    console.log(ev.dataTransfer.files[i])
                }
            }
        },
        loadType: function (type) {
            worker.postMessage({
                action: 'loadType',
                type: type
            })
        },
        process: function (file) {
            this.state.file = file.name
            this.state.processStatus = 'Pre-processing...'
            this.state.processPercentage = 100
            this.file = file

            const reader = new FileReader()
            reader.onload = async (e) => {
                const data = reader.result
                // Process for visualization
                worker.postMessage({
                    action: 'parse',
                    file: data,
                    isTlog: (file.name.endsWith('tlog')),
                    isDji: (file.name.endsWith('txt'))
                })

                try {
                    // Upload to backend
                    const formData = new FormData()
                    formData.append(
                        'file',
                        new Blob([data], { type: 'application/octet-stream' }),
                        file.name
                    )
                    const response = await fetch('http://localhost:8000/api/upload', {
                        method: 'POST',
                        body: formData
                    })
                    if (!response.ok) {
                        throw new Error('Failed to upload file')
                    }
                    const responseData = await response.json()
                    if (responseData.fileKey) {
                        this.state.dataLoaded = true
                        this.state.currentFileKey = responseData.fileKey
                        this.$eventHub.$emit('file-loaded', responseData.fileKey)
                        console.log('File processed successfully:', responseData.fileKey)
                    } else {
                        throw new Error(responseData.error || 'Failed to process file')
                    }
                } catch (error) {
                    console.error('Error uploading file:', error)
                    this.clearFlightData()
                }
            }
            reader.onerror = () => {
                console.error('Error reading file')
                this.clearFlightData()
            }
            reader.readAsArrayBuffer(file)
        },
        uploadFile () {
            this.uploadStarted = true
            this.transferMessage = 'Upload Done!'
            this.uploadpercentage = 0
            const formData = new FormData()
            formData.append('file', this.file)

            const request = new XMLHttpRequest()
            request.onload = () => {
                if (request.status >= 200 && request.status < 400) {
                    this.uploadpercentage = 100
                    this.url = request.responseText
                } else {
                    alert('error! ' + request.status)
                    this.uploadpercentage = 100
                    this.transferMessage = 'Error Uploading'
                    console.log(request)
                }
            }
            request.upload.addEventListener('progress', (e) => {
                if (e.lengthComputable) {
                    this.uploadpercentage = 100 * e.loaded / e.total
                }
            }
            , false)
            request.open('POST', '/upload')
            request.send(formData)
        },
        fixData (message) {
            if (message.name === 'GLOBAL_POSITION_INT') {
                message.lat = message.lat / 10000000
                message.lon = message.lon / 10000000
                // eslint-disable-next-line
                message.relative_alt = message.relative_alt / 1000
            }
            return message
        },
        browse () {
            document.getElementById('choosefile').click()
        },
        share () {
            const el = document.createElement('textarea')
            el.value = window.location.host + '/#/v/' + this.url
            document.body.appendChild(el)
            el.select()
            document.execCommand('copy')
            document.body.removeChild(el)
            this.shared = true
        },
        downloadFileFromURL (url) {
            const a = document.createElement('a')
            document.body.appendChild(a)
            a.style = 'display: none'
            a.href = url
            a.download = this.state.file + '-trimmed.' + this.state.logType
            a.click()
            document.body.removeChild(a)
            window.URL.revokeObjectURL(url)
        },
        clearFlightData () {
            this.state.file = null
            this.state.currentFileKey = null
            this.state.dataLoaded = false
            // Only clear history when user clicks home or reloads
            // fetch('http://localhost:8000/api/clear-history', { method: 'POST' })
            //     .catch(error => console.error('Error clearing history:', error))
        }
    },
    mounted () {
        window.addEventListener('message', (event) => {
            if (event.data.type === 'arrayBuffer') {
                worker.postMessage({
                    action: 'parse',
                    file: event.data.data,
                    isTlog: false,
                    isDji: false
                })
            }
        })
        worker.onmessage = (event) => {
            if (event.data.percentage) {
                this.state.processPercentage = event.data.percentage
            } else if (event.data.availableMessages) {
                this.$eventHub.$emit('messageTypes', event.data.availableMessages)
            } else if (event.data.metadata) {
                this.state.metadata = event.data.metadata
            } else if (event.data.messages) {
                this.state.messages = event.data.messages
                this.$eventHub.$emit('messages')
            } else if (event.data.messagesDoneLoading) {
                this.$eventHub.$emit('messagesDoneLoading')
            } else if (event.data.messageType) {
                this.state.messages[event.data.messageType] = event.data.messageList
                this.$eventHub.$emit('messages')
            } else if (event.data.files) {
                this.state.files = event.data.files
                this.$eventHub.$emit('messages')
            } else if (event.data.url) {
                this.downloadFileFromURL(event.data.url)
            }
        }
        const url = document.location.search.split('?file=')[1]
        if (url) {
            this.onLoadSample(decodeURIComponent(url))
        }
    },
    components: {
        VProgress
    }
}
</script>
<style scoped>

    /* NAVBAR */

    #drop_zone {
        padding-top: 25px;
        padding-left: 10px;
        border: 2px dashed #434b52da;
        width: auto;
        height: 100px;
        margin: 20px;
        border-radius: 5px;
        cursor: default;
        background-color: rgba(0, 0, 0, 0);
    }

    #drop_zone:hover {
        background-color: #171e2450;
    }

    .uploadCheckbox {
        margin-left: 20px;
    }

</style>
