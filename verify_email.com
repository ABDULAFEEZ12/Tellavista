<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Email Verification | Tawfiq AI</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f0f8f7;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
        }
        .verify-container {
            background-color: #fff;
            padding: 30px 40px;
            border-radius: 10px;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
            width: 100%;
            max-width: 400px;
        }
        h2 {
            text-align: center;
            color: #2c3e50;
            margin-bottom: 20px;
        }
        label {
            margin: 10px 0 5px;
            display: block;
            color: #333;
        }
        input[type="text"] {
            width: 100%;
            padding: 10px;
            margin-bottom: 15px;
            border-radius: 5px;
            border: 1px solid #ccc;
        }
        .button {
            padding: 12px;
            width: 100%;
            background-color: #1abc9c;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
        }
        .button:hover {
            background-color: #16a085;
        }
        .error {
            color: red;
            text-align: center;
            margin-bottom: 15px;
        }
        .success {
            color: green;
            text-align: center;
            margin-bottom: 15px;
        }
    </style>
</head>
<body>
    <div class="verify-container">
        <h2>Verify Your Email</h2>

        {% if error %}
            <p class="error">{{ error }}</p>
        {% endif %}

        {% if message %}
            <p class="success">{{ message }}</p>
        {% endif %}

        <form method="POST">
            <label for="username">Username</label>
            <input type="text" name="username" id="username" required value="{{ username or '' }}">

            <label for="code">Verification Code</label>
            <input type="text" name="code" id="code" required pattern="\d{6}" title="Enter the 6-digit code" autofocus>

            <button class="button" type="submit">Verify</button>
        </form>
    </div>
</body>
</html>