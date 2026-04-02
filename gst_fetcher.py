import os
import time
import pandas as pd
from playwright.sync_api import sync_playwright

def read_gst_numbers(file_path):
    """Read a list of GSTINs from a text file."""
    if not os.path.exists(file_path):
        print(f"File {file_path} not found. Creating a sample one.")
        with open(file_path, 'w') as f:
            f.write("22AAAAA0000A1Z5\n") # Sample fake GSTIN
        return ["22AAAAA0000A1Z5"]
    
    with open(file_path, 'r') as f:
        return [line.strip() for line in f if line.strip()]

def fetch_gst_data(gst_numbers, output_file="gst_data.csv"):
    """Automate Playwright to fetch GST data."""
    results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        for index, gstin in enumerate(gst_numbers):
            print(f"\n[{index+1}/{len(gst_numbers)}] Processing GSTIN: {gstin}")
            
            try:
                page.goto("https://services.gst.gov.in/services/searchtp")
                page.wait_for_selector("input[type='text']", timeout=10000)
                
                inputs = page.locator("input[type='text']")
                if inputs.count() > 0:
                    inputs.first.focus()
                    inputs.first.clear()
                    # Type like a human so the GST portal registers all events (fixes the "double click search" bug)
                    inputs.first.press_sequentially(gstin, delay=30)
                    print(f"Auto-filled GSTIN: {gstin}")
                    
                    # Force a blur event to tell the page we finished typing the GSTIN
                    inputs.first.blur()
                    
                    # Drop cursor into Captcha text box cleanly
                    if inputs.count() > 1:
                        inputs.nth(1).focus()
                        inputs.nth(1).click()
                        
                else:
                    print("Could not find the input field. Please fill it manually.")
            except Exception as e:
                print(f"Could not auto-fill. Error: {e}")
            
            print("\n" + "="*50)
            print(">>> ACTION REQUIRED: PLEASE SOLVE THE CAPTCHA AND CLICK 'SEARCH'.")
            print(">>> DO NOT CLOSE THE BROWSER. The script will automatically detect when the data loads...")
            print("="*50)
            
            # Polling to detect search success
            extracted_data = {}
            success = False
            
            # Wait up to 3 minutes (180 seconds) for the user to solve captcha
            for i in range(180): 
                try:
                    time.sleep(1)
                    if page.is_closed():
                        print("Browser was closed manually before data was extracted!")
                        break
                    
                    # Check if 'Legal Name of Business' appears on the page
                    page_text = page.locator("body").inner_text()
                    
                    if "Legal Name" in page_text and "Constitution" in page_text:
                        print("\nProfile loaded! Attempting to open the Filing Table...")
                        
                        # Give it a tiny bit of time to render everything
                        time.sleep(1)
                        
                        try:
                            # The button on GST portal is usually "SHOW FILING TABLE" or "Show Return Filing"
                            # We look for a button containing the text "Filing"
                            button = page.locator("button:has-text('FILING TABLE'), button:has-text('Return Filing'), a:has-text('Return Filing')").first
                            if button.is_visible():
                                button.click()
                                print("-> Clicked the 'Show Filing Table' button.")
                                time.sleep(1) # Wait for the Year/Search area to expand
                                
                                # Now we must click the SECOND "Search" button that appears 
                                # to actually generate the filing table!
                                search_buttons = page.locator("button:has-text('SEARCH'), button:has-text('Search')")
                                if search_buttons.count() > 1:
                                    search_buttons.last.click()
                                    print("-> Clicked the inner 'Search' button to fetch the table!")
                                else:
                                    # Fallback
                                    search_buttons.last.click()
                                    
                                time.sleep(3) # Wait for the table data to fetch and render
                            else:
                                print("-> Could not automatically see the 'Show Filing Table' button. Please click it manually if needed.")
                                time.sleep(3) # Give you 3 seconds to click it yourself
                        except Exception as e:
                            print(f"-> Note: Could not click Filing Table buttons. Error: {e}")
                            
                        print("Extracting GSTR filing dates from the bottom table...")
                        
                        extracted_data = page.evaluate('''() => {
                            let data = {};
                            
                            // 1. Grab the Legal Name just for reference
                            let labels = document.querySelectorAll('label, .control-label, strong, th');
                            labels.forEach(lbl => {
                                let key = lbl.innerText.replace(/:/g, '').trim();
                                if (key === 'Legal Name of Business' || key === 'Legal Name') {
                                    let valElem = lbl.nextElementSibling;
                                    if(valElem && valElem.innerText.trim()) {
                                        data['Legal Name'] = valElem.innerText.trim();
                                    } else if (lbl.parentElement && lbl.parentElement.nextElementSibling) {
                                        data['Legal Name'] = lbl.parentElement.nextElementSibling.innerText.trim();
                                    }
                                }
                            });
                            
                            // 2. Extract the VERY LATEST (first row) from the GSTR-1 and GSTR-3B tables
                            let gstr1_latest = "Not found";
                            let gstr3b_latest = "Not found";
                            
                            let tables = document.querySelectorAll('table');
                            for (let table of tables) {
                                // Try to determine if this table is for GSTR-1 or GSTR-3B
                                // Check its own textContent or parent div's textContent (since it might be in a tab pane like <div id="gstr1_tab">)
                                let blockText = "";
                                if (table.parentElement && table.parentElement.parentElement) {
                                    blockText = table.parentElement.parentElement.textContent.toUpperCase();
                                } else if (table.parentElement) {
                                    blockText = table.parentElement.textContent.toUpperCase();
                                } else {
                                    blockText = table.textContent.toUpperCase();
                                }
                                
                                let tableIdOrClass = (table.id + " " + table.className).toUpperCase();
                                let parentIdOrClass = table.parentElement ? (table.parentElement.id + " " + table.parentElement.className).toUpperCase() : "";
                                let combinedSearchContext = blockText + " " + tableIdOrClass + " " + parentIdOrClass;
                                
                                let isGSTR1 = combinedSearchContext.includes('GSTR1') || combinedSearchContext.includes('GSTR-1');
                                let isGSTR3B = combinedSearchContext.includes('GSTR3B') || combinedSearchContext.includes('GSTR-3B');
                                
                                // Get the first data row (latest filing)
                                let first_data_row = "";
                                let rows = table.querySelectorAll('tr');
                                for (let r of rows) {
                                    let tds = Array.from(r.querySelectorAll('td')).map(td => td.textContent.trim().replace(/\\n/g, ' '));
                                    if (tds.length >= 2) {
                                        first_data_row = tds.join(" | ");
                                        break; // Only want the very latest one
                                    }
                                }
                                
                                if (first_data_row) {
                                    // Extract ONLY the date (DD/MM/YYYY) using regex
                                    let dateMatch = first_data_row.match(/\\d{2}\\/\\d{2}\\/\\d{4}/);
                                    let dateOnly = dateMatch ? dateMatch[0] : first_data_row;
                                    
                                    if (isGSTR1 && gstr1_latest === "Not found") gstr1_latest = dateOnly;
                                    if (isGSTR3B && gstr3b_latest === "Not found") gstr3b_latest = dateOnly;
                                }
                            }
                            
                            data['GSTR-1 Latest Filing'] = gstr1_latest;
                            data['GSTR-3B Latest Filing'] = gstr3b_latest;
                            
                            return data;
                        }''')
                        success = True
                        break
                    
                except Exception as e:
                    pass
            
            if success:
                data_dict = {"Input_GSTIN": gstin}
                data_dict.update(extracted_data)
                results.append(data_dict)
                print(f"Successfully extracted {len(extracted_data)} fields for {gstin}!")
            else:
                print(f"Failed to extract data for {gstin} within the timeout or browser was closed.")
                results.append({"Input_GSTIN": gstin, "Status": "Failed/Timeout"})
            
        print("\nAll given GST numbers processed!")
        try:
            browser.close()
        except:
            pass
        
    df_new = pd.DataFrame(results)
    
    if os.path.exists(output_file):
        try:
            df_old = pd.read_csv(output_file)
            df_combined = pd.concat([df_old, df_new], ignore_index=True)
            # Remove duplicates by Input_GSTIN, keep the latest one we just fetched
            df_combined.drop_duplicates(subset=['Input_GSTIN'], keep='last', inplace=True)
            df_to_save = df_combined
        except Exception:
            df_to_save = df_new
    else:
        df_to_save = df_new
    
    try:
        df_to_save.to_csv(output_file, index=False)
        print(f"\nSaved all results to -> {os.path.abspath(output_file)}")
    except PermissionError:
        fallback_file = output_file.replace(".csv", f"_{int(time.time())}.csv")
        df_to_save.to_csv(fallback_file, index=False)
        print(f"\n[!] The file {output_file} is currently open in another program.")
        print(f"Saved all results to -> {os.path.abspath(fallback_file)} instead.")

if __name__ == "__main__":
    gst_list_file = "gst_numbers.txt"
    print(f"Looking for '{gst_list_file}' in {os.getcwd()}...")
    
    gstins = read_gst_numbers(gst_list_file)
    print(f"Found {len(gstins)} GST number(s) to process.")
    
    output_filename = "gst_extracted_data.csv"
    fetch_gst_data(gstins, output_file=output_filename)
