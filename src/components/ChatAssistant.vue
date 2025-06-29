<template>
  <div class="chat-assistant">
    <!-- Chat messages display -->
    <div class="chat-messages" ref="messagesContainer">
      <div v-if="!isFileLoaded" class="chat-info-message">
        Please upload a flight log or open a sample to ask flight-specific questions.
      </div>
      <div v-for="(message, index) in store.chatHistory" :key="index"
           :class="['message', message.role]">
        <div class="message-content">
          {{ message.content }}
        </div>
      </div>
      <div v-if="store.isChatLoading" class="message assistant">
        <div class="message-content">
          <div class="loading-dots">
            <span></span>
            <span></span>
            <span></span>
          </div>
        </div>
      </div>
      <div v-if="isEmbedding" class="message system">
        <div class="message-content">
          <div class="loading-circle"></div>
          Creating embeddings for flight log analysis...
        </div>
      </div>
    </div>

    <!-- Input area -->
    <div class="chat-input">
      <textarea
        v-model="userInput"
        @keydown.enter.prevent="handleSubmit"
        placeholder="Ask a question about the flight data or platform..."
        rows="3"
      ></textarea>
      <button
        @click="handleSubmit"
        :disabled="!userInput.trim() || store.isChatLoading"
        class="submit-btn"
      >
        Send
      </button>
    </div>
  </div>
</template>

<script>
import { store } from './Globals.js'

export default {
    name: 'ChatAssistant',
    data () {
        return {
            userInput: '',
            store,
            ws: null,
            isEmbedding: false
        }
    },
    computed: {
        isFileLoaded () {
            const isLoaded = this.store.dataLoaded === true && this.store.currentFileKey !== null
            console.log('isFileLoaded check:', {
                dataLoaded: this.store.dataLoaded,
                currentFileKey: this.store.currentFileKey,
                result: isLoaded
            })
            return isLoaded
        }
    },
    created () {
        this.$eventHub.$on('file-loaded', this.handleFileLoaded)
        this.$eventHub.$on('clear-chat', this.handleClearChat)
        // Connect to WebSocket
        this.ws = new WebSocket('ws://localhost:8000/ws')
        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data)
            if (data.type === 'embedding_status') {
                if (data.message.includes('Creating embeddings')) {
                    this.isEmbedding = true
                } else if (data.message.includes('successfully')) {
                    this.isEmbedding = false
                    this.store.chatHistory.push({
                        role: 'system',
                        content: data.message
                    })
                }
                this.$nextTick(() => {
                    this.scrollToBottom()
                })
            }
        }
    },
    beforeDestroy () {
        this.$eventHub.$off('file-loaded')
        this.$eventHub.$off('clear-chat')
        if (this.ws) {
            this.ws.close()
        }
    },
    methods: {
        handleFileLoaded (fileKey) {
            console.log('File loaded with key:', fileKey)
            this.store.dataLoaded = true
            this.store.currentFileKey = fileKey
            // Add a system message to indicate file is loaded
            this.store.chatHistory.push({
                role: 'system',
                content: 'Flight log loaded successfully. You can now ask questions about the flight data. ' +
                    'For example:\n' +
                    '• "What was the highest altitude reached during the flight?"\n' +
                    '• "When did the GPS signal first get lost?"\n' +
                    '• "What was the maximum battery temperature?"\n' +
                    '• "How long was the total flight time?"\n' +
                    '• "List all critical errors that happened mid-flight."\n' +
                    '• "When was the first instance of RC signal loss?"'
            })
        },
        handleClearChat () {
            this.store.currentFileKey = null
            this.store.dataLoaded = false
            this.store.chatHistory = []
        },
        async handleSubmit () {
            if (!this.userInput.trim() || this.store.isChatLoading) return

            const userMessage = this.userInput.trim()
            this.userInput = ''

            // Add user message to chat history
            this.store.chatHistory.push({
                role: 'user',
                content: userMessage
            })

            // Set loading state
            this.store.isChatLoading = true

            try {
                // Always send the question to the backend, let backend/LLM decide how to answer
                const telemetryData = this.getTelemetryData()
                const response = await fetch('http://localhost:8000/api/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        Accept: 'application/json'
                    },
                    body: JSON.stringify({
                        message: userMessage || '',
                        fileKey: this.store.currentFileKey || '',
                        telemetryData: telemetryData || null,
                        chatHistory: this.store.chatHistory || []
                    })
                })

                if (!response.ok) {
                    throw new Error('Failed to get response from chat service')
                }

                const data = await response.json()

                // Add assistant response to chat history
                this.store.chatHistory.push({
                    role: 'assistant',
                    content: data.response
                })
            } catch (error) {
                console.error('Error in chat:', error)
                this.store.chatHistory.push({
                    role: 'assistant',
                    content: 'Sorry, I encountered an error while processing your request. Please try again.'
                })
            } finally {
                this.store.isChatLoading = false
                this.scrollToBottom()
            }
        },

        getTelemetryData () {
            // Get relevant telemetry data from the store
            return {
                trajectory: this.store.trajectories,
                timeTrajectory: this.store.timeTrajectory,
                timeAttitude: this.store.timeAttitude,
                timeAttitudeQ: this.store.timeAttitudeQ,
                flightModeChanges: this.store.flightModeChanges,
                events: this.store.events,
                metadata: this.store.metadata
            }
        },

        scrollToBottom () {
            this.$nextTick(() => {
                const container = this.$refs.messagesContainer
                container.scrollTop = container.scrollHeight
            })
        }
    },
    watch: {
        'store.chatHistory': {
            handler () {
                this.scrollToBottom()
            },
            deep: true
        }
    }
}
</script>

<style scoped>
.chat-assistant {
    width: 100%;
    margin: 0 5px 5px 0;
    background: white;
    border-radius: 10px;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
    display: flex;
    flex-shrink: 0;
    flex-direction: column;
    height: 430px;
    max-width: 100%;
    margin-top: auto;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 5px;
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
}

.message {
  font-size: 0.8em;
  max-width: 80%;
  border-radius: 16px;
  justify-content: center;
  margin: 0;
  box-sizing: border-box;
  display: block;
  align-items: center;
}

.message-content {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 10px 10px 10px;
  min-height: 10px;
  word-wrap: break-word;
  white-space: pre-line;
  box-sizing: border-box;
}

.message.user {
  align-self: flex-end;
  justify-content: center;
  background-color: #007bff;
  color: white;
}

.message.assistant {
  align-self: flex-start;
  background-color: #f0f0f0;
  color: #333;
}

.message.system {
  align-self: center;
  background-color: #e9ecef;
  color: #495057;
  font-style: italic;
  text-align: center;
  border-radius: 16px;
  margin: 8px 0;
}

.chat-input {
  font-size: 0.8em;
  padding: 7px;
  border-top: 1px solid #eee;
  display: flex;
  gap: 10px;
}

textarea {
  flex: 1;
  padding: 10px;
  border: 1px solid #ddd;
  border-radius: 5px;
  resize: none;
  font-family: inherit;
}

textarea:disabled {
  background-color: #f5f5f5;
  cursor: not-allowed;
}

.submit-btn {
  padding: 10px 20px;
  background-color: #007bff;
  color: white;
  border: none;
  border-radius: 5px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.submit-btn:hover:not(:disabled) {
  background-color: #0056b3;
}

.submit-btn:disabled {
  background-color: #cccccc;
  cursor: not-allowed;
}

.loading-dots {
  display: flex;
  gap: 4px;
  justify-content: center;
  align-items: center;
  height: 20px;
}

.loading-dots span {
  width: 8px;
  height: 8px;
  background-color: #666;
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out;
}

.loading-dots span:nth-child(1) { animation-delay: -0.32s; }
.loading-dots span:nth-child(2) { animation-delay: -0.16s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.chat-info-message {
    color: #888;
    font-size: 0.8em;
    margin-bottom: 10px;
    text-align: center;
}

.loading-circle {
  display: inline-block;
  width: 25px;
  height: 25px;
  border: 3px solid #f3f3f3;
  border-top: 3px solid #3498db;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-right: 10px;
  vertical-align: middle;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
</style>
