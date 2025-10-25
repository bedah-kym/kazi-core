### SYS DESIGN FOR MATHIA PROJECT
This project is designed to create an interactive chatbot interface for users to communicate with each other and with an AI-powered assistant. The system is built using Django for the backend and HTML/CSS/JavaScript for the frontend. The chatbot supports real-time messaging, emoji integration, and a responsive design for various devices.
The main components of the system include:
1. **Frontend (HTML/CSS/JavaScript)**:
   - User interface for chat interactions.
   - Real-time message display and input handling.
   - Emoji picker integration.
2. **Backend (Django)**:
   - Handles user authentication and session management.
   - Manages WebSocket connections for real-time communication.
   - Processes incoming messages and generates responses using AI models.
3. **WebSocket Communication**:
   - Enables real-time messaging between users and the chatbot.
4. **Database**:
   - Stores user data, chat history, and message logs.
5. **AI Integration**:
   - Utilizes AI models to generate responses based on user input.
### SETUP INSTRUCTIONS
1. Clone the repository to your local machine.
2. Navigate to the project directory and create a virtual environment.
3. Install the required dependencies using `pip install -r requirements.txt`.
4. Set up the database by running migrations with `python manage.py migrate`.
5. Start the Django development server using `python manage.py runserver`.
6. Open your web browser and navigate to `http://localhost:8000` to access the chatbot interface.
### USAGE
1. Register or log in to your account.
2. Start a new chat session or join an existing one.
3. Type your messages in the input box and hit enter to send.
4. Use the emoji picker to add emojis to your messages.
5. Interact with the AI-powered chatbot for assistance or information.
### CONTRIBUTING
1. Fork the repository and create a new branch for your feature or bug fix.
2. Make your changes and ensure that the code follows the project's coding standards.
3. Test your changes thoroughly.
4. Submit a pull request with a detailed description of your changes.

### Mermaid Diagram for userto user communication
```mermaid  
sequenceDiagram
    participant User1
    participant WebSocketServer
    participant User2

    User1->>WebSocketServer: Send message
    WebSocketServer->>User2: Forward message
    User2->>WebSocketServer: Send reply
    WebSocketServer->>User1: Forward reply  
```
### Mermaid Diagram for user to AI communication
```mermaid
sequenceDiagram
    participant User
    participant WebSocketServer
    participant AI

    User->>WebSocketServer: Send message
    WebSocketServer->>AI: Forward message
    AI->>WebSocketServer: Generate response
    WebSocketServer->>User: Forward response    
```

### mermmaid for the overall system architecture as is in the codebase 
```mermaid
graph TD;
    A[User] -->|Sends Message| B(Chatbot);
    B -->|Generates Response| A;
    B -->|Uses| C[AI Model];
    B -->|Communicates via| D[WebSocket];
    B -->|Stores Data in| E[Database];
    A -->|Interacts with| F[Frontend HTML/CSS/JS];
    B -->|Runs on| G[Backend Django];
    C -->|Processes Data from| E;
    D -->|Enables Real-time Communication| F;
    G -->|Handles Logic and Routing| F;
    G -->|Manages WebSocket Connections| D;
    G -->|Interfaces with| C;
    G -->|Performs CRUD Operations on| E;
    F -->|Sends Requests to| G;
    F -->|Displays Data from| B;
```
### CHANGES MADE
- Separated JavaScript variables into their own script block in the HTML file for better organization.
- Adjusted CSS properties to improve chat message alignment and chat history height for better user experience.
