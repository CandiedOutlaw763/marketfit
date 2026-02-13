# Marketfit 🚀

> **Validate Business Ideas with Real Data**

**Marketfit** is a web application designed to help entrepreneurs and developers find profitable app ideas by analyzing real user discussions. Instead of guessing what people want, Marketfit scrapes platforms like **HackerNews** and **Reddit** to identify genuine pain points, complaints, and feature requests.

## 🔗 Live Demo

Check out the running application here:
👉 **[https://marketfit.onrender.com/](https://marketfit.onrender.com/)**

## 💡 What It Does

Marketfit automates the process of market research by:
1.  **Scraping Discussions:** It pulls data from community hubs where users discuss software and technology.
2.  **Identifying Pain Points:** It analyzes comments to find recurring problems or "hair-on-fire" needs.
3.  **Generating Ideas:** Based on the analyzed data, it suggests concrete app ideas or micro-SaaS opportunities that solve these specific problems.

## 🛠️ Tech Stack

* **Backend:** Python, Flask
* **Scraping:** Custom Python scripts (Reddit/HackerNews integration)
* **Frontend:** HTML, CSS, JavaScript
* **Deployment:** Render

## 🚀 How to Run Locally

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/CandiedOutlaw763/marketfit.git
    cd marketfit
    ```

2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Set Up Environment Variables:**
    Create a `.env` file in the root directory and add necessary API keys (Groq).

4.  **Run the Application:**
    ```bash
    flask run
    ```

5.  **Open in Browser:**
    Navigate to `http://127.0.0.1:5000`.

## 📄 License

This project is open-source. Feel free to fork, contribute, or use it to find your next big idea!
