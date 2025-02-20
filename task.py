import os
import json
import sys
import threading
import time
import shutil
from queue import Queue, Empty
from datetime import datetime, timedelta, date
import pytz
import pandas as pd
from jira import JIRA
from dateutil import parser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import webbrowser  # 웹 브라우저 열기 위한 모듈 추가
import re
from tkcalendar import DateEntry  # 날짜 선택 위젯 추가
import traceback  # 예외 추적을 위한 모듈 추가

# PyInstaller 환경에서의 리소스 경로 처리 함수
def resource_path(relative_path):
    """PyInstaller로 패키징된 경우 임시 폴더에서 데이터 파일을 찾고, 그렇지 않으면 현재 디렉토리에서 찾습니다."""
    try:
        # PyInstaller로 패키징된 경우
        base_path = sys._MEIPASS
    except Exception:
        # 개발 환경에서 실행 중인 경우
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# URL 검출 함수 정의
def extract_url(text):
    url_regex = re.compile(r'(https?://\S+)', re.IGNORECASE)
    urls = url_regex.findall(text)
    return urls[0] if urls else None

# URL 검출 함수 정의 (comment용)
def extract_urls_from_comment(comment):
    """
    댓글에서 다양한 형식의 URL과 관련 정보를 추출합니다.
    """
    urls = []
    
    # 패턴 1: [URL|URL|smart-link]
    pattern1 = re.compile(r'\[(https?://[^\|\]]+)\|https?://[^\|\]]+\|[^\]]+\]')
    urls.extend(pattern1.findall(comment))
    
    # 패턴 2: Swarm Link: http://perforce.alt9.io/changes/62401
    pattern2 = re.compile(r'Swarm Link:\s*(https?://\S+)')
    urls.extend(pattern2.findall(comment))
    
    # 패턴 3: This issue links to "Commit - fix: add passive when log in #SM7-2749 (Web Link)"
    pattern3 = re.compile(r'This issue links to\s*\"(.+?)\"')
    links = pattern3.findall(comment)
    for link in links:
        urls.append(link)
    
    return urls

# Committer 추출 함수
def extract_committer(comment, author_name='Unknown'):
    """
    댓글에서 Committer 정보를 추출합니다.
    1. Committer: 이름 형식 (예: Committer: cucryma)
    2. Change ... by 아이디 on ... 형식 (예: Change 60180 by jenkins@jenkins-master-Sol_Replicate_Proto_ToP4-Dev1 on 2024/10/18 04:48:10)
    3. 둘 다 없는 경우, 'Unknown' 반환
    """
    committers = []
    
    # 패턴 1: Committer: 이름
    pattern1 = re.compile(r'Committer:\s*(\S+)')
    matches1 = pattern1.findall(comment)
    committers.extend(matches1)
    
    # 패턴 2: Change ... by 아이디 on ...
    pattern2 = re.compile(r'Change\s+\d+\s+by\s+(\S+)@')
    matches2 = pattern2.findall(comment)
    committers.extend(matches2)
    
    if committers:
        # Committer가 있는 경우 첫 번째 매치 반환
        return committers[0]
    else:
        return 'Unknown'

# Swarm Link 추출 함수
def extract_swarm_link(comment):
    """
    댓글에서 Swarm Link를 추출합니다.
    """
    pattern = re.compile(r'Swarm Link:\s*(https?://\S+)')
    match = pattern.search(comment)
    return match.group(1) if match else ''

# 자격 증명 파일에서 인증 정보 가져오기
def load_jira_credentials():
    credentials_path = os.path.join(os.getcwd(), 'jira_credentials.json')
    if not os.path.exists(credentials_path):
        return None
    try:
        with open(credentials_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        messagebox.showerror("파일 오류", "jira_credentials.json 파일의 형식이 잘못되었습니다.")
        return None

# JIRA 자격 증명 정보 로드
credentials = load_jira_credentials()

if credentials:
    JIRA_URL = credentials.get('JIRA_URL')
    JIRA_USERNAME = credentials.get('JIRA_USERNAME')
    JIRA_API_TOKEN = credentials.get('JIRA_API_TOKEN')
else:
    JIRA_URL = None
    JIRA_USERNAME = None
    JIRA_API_TOKEN = None

# 1. GUI 설정
class JiraTrackerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("JIRA Issue Tracker")
        self.root.geometry("400x700")  # 높이를 늘려줍니다.

        # 초기 실행 시 자격 증명 확인
        if not credentials:
            self.prompt_credentials()
        else:
            self.setup_gui()

    def prompt_credentials(self):
        """사용자로부터 JIRA 자격 증명을 입력받는 GUI 창을 띄웁니다."""
        self.credentials_window = tk.Toplevel(self.root)
        self.credentials_window.title("JIRA 자격 증명 입력")
        self.credentials_window.geometry("400x300")
        self.credentials_window.grab_set()  # 모달 창으로 만듭니다.

        ttk.Label(self.credentials_window, text="JIRA URL:").pack(pady=5)
        self.jira_url_entry = ttk.Entry(self.credentials_window, width=50)
        self.jira_url_entry.pack(pady=5)

        ttk.Label(self.credentials_window, text="사용자 이름:").pack(pady=5)
        self.jira_username_entry = ttk.Entry(self.credentials_window, width=50)
        self.jira_username_entry.pack(pady=5)

        ttk.Label(self.credentials_window, text="API 토큰:").pack(pady=5)
        self.jira_api_token_entry = ttk.Entry(self.credentials_window, width=50, show="*")
        self.jira_api_token_entry.pack(pady=5)

        ttk.Button(self.credentials_window, text="저장", command=self.save_credentials).pack(pady=20)

    def save_credentials(self):
        """입력받은 자격 증명을 저장하고 GUI를 설정합니다."""
        jira_url = self.jira_url_entry.get().strip()
        jira_username = self.jira_username_entry.get().strip()
        jira_api_token = self.jira_api_token_entry.get().strip()

        if not jira_url or not jira_username or not jira_api_token:
            messagebox.showerror("입력 오류", "모든 필드를 입력해주세요.")
            return

        credentials = {
            "JIRA_URL": jira_url,
            "JIRA_USERNAME": jira_username,
            "JIRA_API_TOKEN": jira_api_token
        }

        credentials_path = os.path.join(os.getcwd(), 'jira_credentials.json')
        try:
            with open(credentials_path, 'w', encoding='utf-8') as f:
                json.dump(credentials, f, ensure_ascii=False, indent=4)
            messagebox.showinfo("성공", "자격 증명이 저장되었습니다.")
            self.credentials_window.destroy()
            self.setup_gui()
        except Exception as e:
            messagebox.showerror("저장 오류", f"자격 증명을 저장하는 중 오류가 발생했습니다:\n{e}")

    def setup_gui(self):
        """기본 GUI를 설정합니다."""
        # 조회 시간 설정
        ttk.Label(self.root, text="조회 범위 (시간):").pack(pady=5)
        self.hours_entry = ttk.Entry(self.root)
        self.hours_entry.pack(pady=5)

        # 담당자 이름 설정
        ttk.Label(self.root, text="담당자 이름 (옵션):").pack(pady=5)
        self.assignee_entry = ttk.Entry(self.root)
        self.assignee_entry.pack(pady=5)

        # 변경한 사람 설정 (기존 키워드 필터링을 대체)
        ttk.Label(self.root, text="변경한 사람 (옵션):").pack(pady=5)
        self.author_entry = ttk.Entry(self.root)
        self.author_entry.pack(pady=5)

        # 지정 날짜 설정
        ttk.Label(self.root, text="지정 날짜 (옵션):").pack(pady=5)
        self.date_entry = DateEntry(self.root, width=12, background='darkblue', foreground='white', borderwidth=2, date_pattern='yyyy-mm-dd')
        self.date_entry.pack(pady=5)
        self.date_entry.bind("<<DateEntrySelected>>", self.on_date_change)  # 날짜 선택 이벤트 바인딩

        # 초기에는 지정 날짜가 선택되지 않은 것으로 설정
        self.date_selected = False

        # All Issues 체크박스
        self.all_issues_var = tk.BooleanVar()
        ttk.Checkbutton(self.root, text="전체 이슈 수집", variable=self.all_issues_var).pack(pady=5)

        # 실행 버튼
        ttk.Button(self.root, text="실행", command=self.run_tracker).pack(pady=10)

        # 결과 보기 버튼
        ttk.Button(self.root, text="결과 보기", command=self.show_results).pack(pady=10)

        # Export 버튼
        ttk.Button(self.root, text="Export", command=self.export_results).pack(pady=10)

        self.df = None  # 결과를 저장할 DataFrame

    def on_date_change(self, event):
        # 지정 날짜가 오늘 날짜와 다르면 날짜가 선택된 것으로 간주
        selected_date = self.date_entry.get_date()
        if selected_date != date.today():
            self.date_selected = True
            # 조회 범위 (시간) 입력 필드 비활성화
            self.hours_entry.configure(state='disabled')
        else:
            self.date_selected = False
            # 조회 범위 (시간) 입력 필드 활성화
            self.hours_entry.configure(state='normal')

    def run_tracker(self):
        if not self.date_selected:
            try:
                hours = float(self.hours_entry.get())
            except ValueError:
                messagebox.showerror("입력 오류", "조회 범위를 숫자로 입력해야 합니다.")
                return
            selected_date = None
        else:
            hours = None  # 지정 날짜가 선택된 경우 조회 범위는 사용하지 않음
            selected_date = self.date_entry.get_date()

        assignee_name = self.assignee_entry.get().strip()
        author_name = self.author_entry.get().strip()
        all_issues = self.all_issues_var.get()

        # 실행 중 팝업
        self.running_popup = tk.Toplevel(self.root)
        self.running_popup.title("실행 중")
        self.running_popup.geometry("200x100")
        ttk.Label(self.running_popup, text="실행 중입니다...잠시만 기다려주세요.").pack(expand=True)

        # 백그라운드 스레드에서 실행
        thread = threading.Thread(target=self.run_tracker_thread, args=(hours, all_issues, assignee_name, author_name, selected_date))
        thread.start()

    def run_tracker_thread(self, hours, all_issues_flag, assignee_name, author_name, selected_date):
        try:
            self.df = run_jira_tracker(hours, all_issues_flag, assignee_name, author_name, selected_date)
            if self.df is not None and not self.df.empty:
                n = len(self.df)  # 수집된 이력의 개수 계산
                message = f"JIRA 변경 사항 추적이 완료되었습니다.\n총 {n}개 이력이 수집되었습니다."
                self.root.after(0, lambda: messagebox.showinfo("완료", message))
            else:
                self.root.after(0, lambda: messagebox.showinfo("완료", "조건에 해당하는 변경 사항이 없습니다."))
        except Exception as e:
            # 예외의 전체 정보를 출력하도록 수정
            error_message = ''.join(traceback.format_exception(None, e, e.__traceback__))
            self.root.after(0, lambda: messagebox.showerror("오류", f"오류가 발생했습니다:\n{error_message}"))
            self.df = None
        finally:
            self.running_popup.destroy()

    def show_results(self):
        if self.df is not None and not self.df.empty:
            # 표시할 컬럼만 선택 (Committer, Swarm Link, 담당자 추가)
            display_columns = ['# 키', '유형', '요약', '이슈 필드', '변경 전 내용', '변경 후 내용', '변경 시간', '변경한 사람', '담당자', 'Committer', 'Swarm Link']
            display_df = self.df[display_columns]

            # 모든 결측값(NaN, NaT, None)을 '-'로 대체
            display_df = display_df.fillna('-')

            # 팝업 창에 결과 표시
            result_window = tk.Toplevel(self.root)
            result_window.title("변경 사항 결과")
            result_window.geometry("1800x800")  # 충분한 크기로 조정

            # 검색 프레임 추가
            search_frame = ttk.Frame(result_window)
            search_frame.pack(side='top', fill='x', padx=10, pady=5)

            ttk.Label(search_frame, text="검색어:").pack(side='left', padx=5)
            search_entry = ttk.Entry(search_frame)
            search_entry.pack(side='left', fill='x', expand=True, padx=5)

            def search():
                query = search_entry.get().strip()
                if not query:
                    # 검색어가 비어있으면 전체 데이터 로드
                    self.update_treeview(tree, display_df)
                    return
                # 쉼표로 키워드 분리 및 공백 제거
                keywords = [kw.strip() for kw in query.split(',') if kw.strip()]
                if not keywords:
                    messagebox.showinfo("가이드", "유효한 검색어를 입력해주세요. 다중 검색은 ,로 구분합니다.")
                    return
                filtered_df = display_df.copy()
                for kw in keywords:
                    filtered_df = filtered_df[
                        filtered_df.apply(lambda row: row.astype(str).str.contains(kw, case=False).any(), axis=1)
                    ]
                # Treeview 업데이트
                self.update_treeview(tree, filtered_df)

            ttk.Button(search_frame, text="검색", command=search).pack(side='left', padx=5)

            # Treeview와 스크롤바를 포함할 프레임 생성
            tree_frame = ttk.Frame(result_window)
            tree_frame.pack(expand=True, fill='both', padx=10, pady=5)

            # Treeview 생성
            tree = ttk.Treeview(tree_frame, show='headings')
            tree.pack(side='left', expand=True, fill='both')

            # 스크롤바 추가
            vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
            vsb.pack(side='right', fill='y')
            hsb = ttk.Scrollbar(result_window, orient="horizontal", command=tree.xview)
            hsb.pack(side='bottom', fill='x')
            tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

            # 컬럼 정의
            tree['columns'] = display_columns

            for col in display_columns:
                tree.heading(col, text=col, anchor=tk.W)
                tree.column(col, anchor=tk.W, width=200)  # 넓이를 충분히 설정

            # 스타일 정의
            style = ttk.Style()
            style.theme_use('default')

            # Treeview 스타일 설정
            style.configure("Custom.Treeview",
                            background="#FFFFFF",
                            foreground="#000000",
                            rowheight=25,
                            fieldbackground="#FFFFFF")
            style.map('Custom.Treeview', background=[('selected', '#BFBFBF')])

            # 그리드 라인 표시 및 색상 설정
            style.layout("Custom.Treeview", [('Custom.Treeview.treearea', {'sticky': 'nswe'})])
            style.configure("Custom.Treeview", bordercolor="#BFBFBF", relief="flat")
            style.configure("Custom.Treeview.Heading", bordercolor="#BFBFBF", relief="flat")
            style.map("Custom.Treeview", bordercolor=[('selected', '#BFBFBF')])

            # Treeview에 스타일 적용
            tree.configure(style="Custom.Treeview")

            # Treeview에 볼드체 태그 정의
            bold_font = ("TkDefaultFont", 10, "bold")
            style.configure("Bold.Treeview", font=bold_font)
            tree.tag_configure('bold', font=bold_font)

            # Treeview 초기 데이터 채우기
            self.update_treeview(tree, display_df)

            # 이벤트 바인딩 추가
            tree.bind('<ButtonRelease-1>', self.on_tree_item_click)

        else:
            messagebox.showwarning("경고", "먼저 변경 사항을 추적해주세요.")

    def update_treeview(self, tree, data):
        """
        Treeview를 업데이트하는 메서드.
        기존 항목을 모두 제거하고, 새로운 데이터를 삽입합니다.
        """
        # 기존 항목 모두 제거
        for item in tree.get_children():
            tree.delete(item)
        
        # 데이터 삽입 및 색상 코딩
        for idx, row in data.iterrows():
            issue_type = row['유형']
            issue_field = row['이슈 필드']
            tags = ()
            if issue_type in [
                '휴지통(최상위일감)', '대분류', '아트 영역 분류'
            ]:
                tags = ('top_issue',)
            elif issue_type in [
                '휴지통(에픽)', '아웃소싱 캐릭터모델링', '아트 배경 일감', '아웃소싱 캐릭터컨셉', 'Epic',
                '아트 UI 일감', '아웃소싱 배경모델링', '그룹', '아트 캐릭터 일감', '요청/발주'
            ]:
                tags = ('upper_issue',)
            # '삭제된 이슈' 또는 '생성된 이슈'인 경우 'bold' 태그 추가
            if issue_field in ['삭제된 이슈', '생성된 이슈']:
                tags = tags + ('bold',)
            tree.insert('', 'end', values=list(row), tags=tags)

    def on_tree_item_click(self, event):
        # 클릭한 영역 확인
        region = event.widget.identify_region(event.x, event.y)
        if region != 'cell':
            # 셀이 아닌 영역(헤더 등)을 클릭한 경우 이벤트 무시
            return

        item_id = event.widget.focus()
        if item_id:
            item = event.widget.item(item_id)
            values = item.get('values', [])
            if values:
                # 컬럼 이름 리스트 (Committer, Swarm Link, 담당자 포함)
                columns = ['# 키', '유형', '요약', '이슈 필드', '변경 전 내용', '변경 후 내용', '변경 시간', '변경한 사람', '담당자', 'Committer', 'Swarm Link']
                # 클릭한 컬럼의 인덱스
                column = event.widget.identify_column(event.x)
                column_index = int(column.replace('#', '')) - 1  # '#1'부터 시작하므로 -1

                if column_index < len(columns):
                    column_name = columns[column_index]
                    if column_name == '# 키':
                        # 원본 DataFrame에서 이슈 URL 가져오기
                        issue_key = values[column_index]
                        issue_url_series = self.df.loc[self.df['# 키'] == issue_key, '이슈 URL']
                        if not issue_url_series.empty:
                            issue_url = issue_url_series.values[0]
                            if pd.notna(issue_url) and issue_url != '':
                                webbrowser.open(issue_url)
                    elif column_name in ['변경 전 내용', '변경 후 내용']:
                        # 변경 내용의 URL 가져오기
                        issue_key = values[0]  # 첫 번째 컬럼이 '# 키'
                        changed_content = values[column_index]
                        # 해당 이슈와 변경 내용이 일치하는 행 찾기
                        mask = (self.df['# 키'] == issue_key) & (self.df[column_name].astype(str) == str(changed_content))
                        url_column = f"{column_name} URL"
                        url_series = self.df.loc[mask, url_column]
                        if not url_series.empty:
                            url = url_series.values[0]
                            if pd.notna(url) and url != '':
                                webbrowser.open(url)
                    elif column_name in ['Committer', 'Swarm Link', '담당자']:
                        # Committer, Swarm Link, 담당자 클릭 시 해당 정보 표시 또는 동작 추가 가능
                        if column_name == 'Committer':
                            committer = values[column_index]
                            if committer != 'Unknown' and committer != '-':
                                # 예시: Committer의 프로필 URL 패턴이 있다면 여기에 추가
                                # 예: f"https://yourdomain.atlassian.net/people/{committer}"
                                # 여기서는 가상의 URL을 사용
                                profile_url = f"https://yourdomain.atlassian.net/people/{committer}"
                                webbrowser.open(profile_url)
                        elif column_name == 'Swarm Link':
                            swarm_link = values[column_index]
                            if swarm_link != '-':
                                webbrowser.open(swarm_link)
                        elif column_name == '담당자':
                            assignee = values[column_index]
                            if assignee != '-' and assignee != 'Unknown':
                                # Assignee의 프로필 URL 패턴이 있다면 여기에 추가
                                # 예: f"https://yourdomain.atlassian.net/people/{assignee}"
                                # 여기서는 가상의 URL을 사용
                                assignee_profile_url = f"https://yourdomain.atlassian.net/people/{assignee}"
                                webbrowser.open(assignee_profile_url)
                    # 링크가 없으면 아무 동작도 하지 않음

    def export_results(self):
        if self.df is not None and not self.df.empty:
            file_path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
            if file_path:
                with pd.ExcelWriter(file_path, engine='xlsxwriter') as writer:
                    # '변경 후 내용 URL'은 Excel에 저장하지 않습니다.
                    export_columns = ['# 키', '유형', '요약', '이슈 필드', '변경 전 내용', '변경 후 내용', '변경 시간', '변경한 사람', '담당자', 'Committer', 'Swarm Link']
                    export_df = self.df[export_columns]
                    export_df.to_excel(writer, index=False, sheet_name='변경 사항')

                    workbook = writer.book
                    worksheet = writer.sheets['변경 사항']

                    # '# 키' 컬럼에 하이퍼링크 설정
                    for row_num, (issue_key, issue_url) in enumerate(zip(self.df['# 키'], self.df['이슈 URL']), start=1):
                        if pd.notna(issue_url) and issue_url != '':
                            worksheet.write_url(row_num, 0, issue_url, string=issue_key)

                    # '변경 후 내용' 컬럼에 하이퍼링크 설정
                    changed_to_col_index = export_df.columns.get_loc('변경 후 내용')
                    for row_num, (changed_to, changed_url) in enumerate(zip(self.df['변경 후 내용'], self.df['변경 후 내용 URL']), start=1):
                        if pd.notna(changed_url) and changed_url != '':
                            worksheet.write_url(row_num, changed_to_col_index, changed_url, string=str(changed_to))

                    # 삭제된 이슈 및 생성된 이슈 볼드체로 표시
                    bold_format = workbook.add_format({'bold': True})
                    for row_num, issue_field in enumerate(self.df['이슈 필드'], start=1):
                        if issue_field in ['삭제된 이슈', '생성된 이슈']:
                            worksheet.set_row(row_num, None, bold_format)

                messagebox.showinfo("저장 완료", f"결과가 {file_path}에 저장되었습니다.")
        else:
            messagebox.showwarning("경고", "먼저 변경 사항을 추적해주세요.")

# 2. JIRA 변경 사항 추적 함수
def run_jira_tracker(hours, all_issues_flag, assignee_name, author_name, selected_date):
    # 자격 증명 정보 가져오기
    credentials_path = os.path.join(os.getcwd(), 'jira_credentials.json')
    if not os.path.exists(credentials_path):
        raise Exception("jira_credentials.json 파일을 찾을 수 없습니다. 자격 증명을 입력해주세요.")
    try:
        with open(credentials_path, 'r', encoding='utf-8') as f:
            credentials = json.load(f)
    except FileNotFoundError:
        raise Exception("jira_credentials.json 파일을 찾을 수 없습니다. 자격 증명을 입력해주세요.")
    except json.JSONDecodeError:
        raise Exception("jira_credentials.json 파일의 형식이 잘못되었습니다.")

    JIRA_URL = credentials.get('JIRA_URL')
    JIRA_USERNAME = credentials.get('JIRA_USERNAME')
    JIRA_API_TOKEN = credentials.get('JIRA_API_TOKEN')

    if not JIRA_URL or not JIRA_USERNAME or not JIRA_API_TOKEN:
        raise Exception("JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN 값을 설정해주세요.")

    # 필드 목록 로드
    fields_to_track_path = resource_path('fields_to_track.json')
    try:
        with open(fields_to_track_path, 'r', encoding='utf-8') as f:
            fields_data = json.load(f)
            fields_to_track = fields_data['fields_to_track']
    except FileNotFoundError:
        raise Exception(f"{fields_to_track_path} 파일을 찾을 수 없습니다. 파일이 있는지 확인해주세요.")
    except json.JSONDecodeError:
        raise Exception(f"{fields_to_track_path} 파일의 형식이 잘못되었습니다.")

    # Jira 연결 설정
    options = {'server': JIRA_URL}
    jira_main = JIRA(options, basic_auth=(JIRA_USERNAME, JIRA_API_TOKEN), max_retries=3)

    # 시간 설정 (KST 기준)
    kst = pytz.timezone('Asia/Seoul')
    now_kst = datetime.now(kst)
    time_format = '%Y-%m-%d %H:%M:%S'

    # 이전 이슈 데이터 로드 (all_issues.json 사용)
    all_issues_path = os.path.join(os.getcwd(), 'all_issues.json')  # 현재 작업 디렉토리에 저장
    try:
        all_issues = load_all_issues(all_issues_path) if all_issues_flag else {}
    except Exception as e:
        print(f"전체 이슈 로드 중 오류 발생: {e}")
        all_issues = {}

    # 현재 이슈 목록 수집
    project_keys = ['SART', 'SM7']
    project_keys_str = ', '.join(f'"{key}"' for key in project_keys)

    # JQL 쿼리 구성
    jql_parts = [f'project IN ({project_keys_str})']

    if assignee_name:
        jql_parts.append(f'assignee = "{assignee_name}"')

    if selected_date:
        # 지정된 날짜부터 현재 시간까지의 범위 설정
        start_date = datetime.combine(selected_date, datetime.min.time()).astimezone(kst)
        end_date = now_kst
        start_date_str = start_date.strftime('%Y/%m/%d %H:%M')
        end_date_str = end_date.strftime('%Y/%m/%d %H:%M')
        jql_parts.append(f'updated >= "{start_date_str}" AND updated <= "{end_date_str}"')
    else:
        if not all_issues_flag and hours is not None:
            # 조회 범위(시간)를 사용
            time_ago_kst = now_kst - timedelta(hours=hours)
            jql_parts.append(f'updated >= "{time_ago_kst.strftime("%Y/%m/%d %H:%M")}"')
        elif not all_issues_flag and hours is None:
            # 조회 범위(시간)을 입력하지 않은 경우 오류 발생
            raise ValueError("조회 범위를 입력하거나 지정 날짜를 선택해주세요.")

    jql = ' AND '.join(jql_parts)

    # 'creator' 필드 추가
    fields = 'summary,issuetype,created,creator,assignee,comment'  # 'creator' 필드 추가

    try:
        issues = jira_main.search_issues(jql, maxResults=False, fields=fields, expand='changelog')
    except Exception as e:
        raise Exception(f"JIRA 이슈 검색 중 오류가 발생했습니다.\nJQL 쿼리: {jql}\n에러 메시지: {e}")

    current_issue_keys = set()
    changes = []
    current_issues = {}
    issue_queue = Queue()

    for issue in issues:
        issue_queue.put(issue)

    lock = threading.Lock()

    # 날짜 형식 감지 및 변환 함수 정의
    def format_if_date(value):
        if not isinstance(value, str):
            return value
        try:
            dt = parser.parse(value)
            return dt.strftime(time_format)
        except (ValueError, TypeError):
            return value

    # RemoteIssueLink를 처리하는 함수 정의 (대체)
    def process_remote_issue_links(issue, changes, now_kst, JIRA_URL, start_date=None, end_date=None):
        """
        댓글에서 링크를 추출하고, 링크의 생성 시간이 지정된 범위 내에 있는 경우에만 추가합니다.
        """
        try:
            if hasattr(issue.fields, 'comment') and issue.fields.comment:
                for comment in issue.fields.comment.comments:
                    comment_body = comment.body
                    comment_author = comment.author.displayName if hasattr(comment.author, 'displayName') else 'Unknown'
                    urls = extract_urls_from_comment(comment_body)
                    committer = extract_committer(comment_body, comment_author)
                    swarm_link = extract_swarm_link(comment_body)

                    # 변경한 사람 필터링: author_name과 일치하는지 확인
                    if author_name and committer != author_name:
                        continue  # 일치하지 않으면 건너뜀

                    # 댓글의 생성 시간이 범위 내에 있는지 확인
                    comment_created = parser.isoparse(comment.created).astimezone(kst)
                    if start_date and comment_created < start_date:
                        continue
                    if end_date and comment_created > end_date:
                        continue

                    # Assignee 정보 추출
                    담당자 = issue.fields.assignee.displayName if issue.fields.assignee and hasattr(issue.fields.assignee, 'displayName') else 'Unknown'

                    for url in urls:
                        changes.append({
                            '# 키': issue.key,
                            '유형': issue.fields.issuetype.name if hasattr(issue.fields, 'issuetype') else 'Unknown',
                            '요약': issue.fields.summary if hasattr(issue.fields, 'summary') else 'Unknown',
                            '이슈 필드': 'CommentLink',
                            '변경 전 내용': '',
                            '변경 후 내용': url,
                            '변경 시간': comment_created.strftime(time_format),
                            '변경한 사람': committer,  # Committer 사용
                            '담당자': 담당자,        # 담당자 추가
                            '이슈 URL': f"{JIRA_URL}/browse/{issue.key}",
                            '변경 전 내용 URL': '',
                            '변경 후 내용 URL': url,
                            'Committer': committer,
                            'Swarm Link': swarm_link
                        })
        except Exception as e:
            print(f"Error processing comment links for issue {issue.key}: {e}")
            traceback.print_exc()

    # 스레드에서 실행할 함수 정의
    def process_issue():
        jira = JIRA(options, basic_auth=(JIRA_USERNAME, JIRA_API_TOKEN), max_retries=3)

        while True:
            try:
                issue = issue_queue.get_nowait()
            except Empty:
                break

            try:
                issue_key = issue.key
                issue_type = issue.fields.issuetype.name if hasattr(issue.fields, 'issuetype') else 'Unknown'
                issue_summary = issue.fields.summary if hasattr(issue.fields, 'summary') else 'Unknown'

                # Assignee 정보 추출
                담당자 = issue.fields.assignee.displayName if issue.fields.assignee and hasattr(issue.fields.assignee, 'displayName') else 'Unknown'

                # 현재 이슈 정보 저장
                with lock:
                    current_issue_keys.add(issue_key)
                    current_issues[issue_key] = {
                        '유형': issue_type,
                        '요약': issue_summary
                    }

                # 이슈 생성 여부 확인
                try:
                    created = parser.isoparse(issue.fields.created).astimezone(kst)
                except Exception:
                    created = now_kst - timedelta(hours=13)

                # 이슈 생성자 이름 가져오기
                creator_name = issue.fields.creator.displayName if hasattr(issue.fields, 'creator') and hasattr(issue.fields.creator, 'displayName') else 'Unknown'

                # 이슈 생성 날짜에 대한 필터링 추가
                include_issue = True
                if selected_date:
                    start_date = datetime.combine(selected_date, datetime.min.time()).astimezone(kst)
                    end_date = now_kst
                    if not (start_date <= created <= end_date):
                        include_issue = False
                elif not all_issues_flag and hours is not None:
                    time_ago_kst = now_kst - timedelta(hours=hours)
                    if not (time_ago_kst <= created <= now_kst):
                        include_issue = False

                # 이슈 생성 날짜가 범위 내에 있을 때만 '생성된 이슈'로 추가
                if issue_key not in all_issues and include_issue:
                    # 변경한 사람 필터링: author_name이 지정되지 않았거나, creator_name이 author_name과 일치할 때만 추가
                    if not author_name or (author_name and creator_name == author_name):
                        with lock:
                            changes.append({
                                '# 키': issue_key,
                                '유형': issue_type,
                                '요약': issue_summary,
                                '이슈 필드': '생성된 이슈',
                                '변경 전 내용': '',
                                '변경 후 내용': created.strftime(time_format),
                                '변경 시간': created.strftime(time_format),
                                '변경한 사람': creator_name,  # creator_name 사용
                                '담당자': 담당자,              # 담당자 추가
                                '이슈 URL': f"{JIRA_URL}/browse/{issue_key}",
                                '변경 전 내용 URL': '',
                                '변경 후 내용 URL': '',
                                'Committer': '',
                                'Swarm Link': ''
                            })

                # 이슈의 변경 이력 가져오기
                try:
                    time.sleep(0.05)
                    issue_detail = jira.issue(issue_key, expand='changelog')
                    changelog = issue_detail.changelog

                    for history in changelog.histories:
                        try:
                            history_created = parser.isoparse(history.created).astimezone(kst)
                        except Exception:
                            continue

                        include_change = True

                        if selected_date:
                            if not (start_date <= history_created <= end_date):
                                include_change = False
                        elif not all_issues_flag and hours is not None:
                            time_ago_kst = now_kst - timedelta(hours=hours)
                            if not (time_ago_kst <= history_created <= now_kst):
                                include_change = False

                        if include_change:
                            # 변경한 사람 필터링: history.author.displayName이 지정된 author_name과 일치하는지 확인
                            history_author = history.author.displayName if hasattr(history.author, 'displayName') else 'Unknown'
                            if author_name and history_author != author_name:
                                continue  # 일치하지 않으면 건너뜀

                            for item in history.items:
                                field_identifier = getattr(item, 'fieldId', item.field)
                                if field_identifier in fields_to_track or item.field.lower() == 'comment':
                                    from_string = str(item.fromString) if item.fromString else ''
                                    to_string = str(item.toString) if item.toString else ''
                                    author_name_history = history_author

                                    # 디버깅 로그 추가
                                    print(f"Processing field: {item.field}")
                                    print(f"From: {from_string}")
                                    print(f"To: {to_string}")
                                    print(f"Author: {author_name_history}")

                                    # 키워드 필터링 제거

                                    from_formatted = format_if_date(from_string)
                                    to_formatted = format_if_date(to_string)

                                    # 변경 전 내용에서 URL 추출
                                    from_url = extract_url(from_string)
                                    # 변경 후 내용에서 URL 추출
                                    to_url = extract_url(to_string)

                                    # Assignee 정보 추출
                                    담당자 = issue.fields.assignee.displayName if issue.fields.assignee and hasattr(issue.fields.assignee, 'displayName') else 'Unknown'

                                    with lock:
                                        changes.append({
                                            '# 키': issue_key,
                                            '유형': issue_type,
                                            '요약': issue_summary,
                                            '이슈 필드': item.field,
                                            '변경 전 내용': from_formatted,
                                            '변경 후 내용': to_formatted,
                                            '변경 시간': history_created.strftime(time_format),
                                            '변경한 사람': author_name_history,
                                            '담당자': 담당자,          # 담당자 추가
                                            '이슈 URL': f"{JIRA_URL}/browse/{issue_key}",
                                            '변경 전 내용 URL': from_url if from_url else '',
                                            '변경 후 내용 URL': to_url if to_url else '',
                                            'Committer': '',
                                            'Swarm Link': ''
                                        })
                except Exception as e:
                    print(f"Error processing issue {issue_key}: {e}")
                    traceback.print_exc()

                # RemoteIssueLink 대신 comment에서 링크 추출 및 필터링
                try:
                    if selected_date:
                        start_date = datetime.combine(selected_date, datetime.min.time()).astimezone(kst)
                        end_date = now_kst
                    else:
                        if all_issues_flag or hours is None:
                            start_date = None
                            end_date = None
                        else:
                            start_date = now_kst - timedelta(hours=hours)
                            end_date = now_kst
                    process_remote_issue_links(issue, changes, now_kst, JIRA_URL, start_date, end_date)
                except Exception as e:
                    print(f"Error processing comment links for issue {issue_key}: {e}")
                    traceback.print_exc()

            except Exception as e:
                print(f"Unhandled exception in thread: {e}")
                traceback.print_exc()

            finally:
                issue_queue.task_done()

    # 스레드 생성 및 시작
    num_threads = 5
    threads = []

    for i in range(num_threads):
        t = threading.Thread(target=process_issue)
        t.start()
        threads.append(t)

    issue_queue.join()

    for t in threads:
        t.join()

    # 삭제된 이슈 검출
    if all_issues_flag:
        deleted_issues = set(all_issues.keys()) - set(current_issue_keys)
        for issue_key in deleted_issues:
            issue_info = all_issues[issue_key]
            changes.append({
                '# 키': issue_key,
                '유형': issue_info.get('유형', ''),
                '요약': issue_info.get('요약', ''),
                '이슈 필드': '삭제된 이슈',
                '변경 전 내용': 'Exists',
                '변경 후 내용': 'Deleted',
                '변경 시간': now_kst.strftime(time_format),
                '변경한 사람': '',
                '담당자': '',               # 담당자 추가
                '이슈 URL': f"{JIRA_URL}/browse/{issue_key}",
                '변경 전 내용 URL': '',
                '변경 후 내용 URL': '',
                'Committer': '',
                'Swarm Link': ''
            })

    if all_issues_flag:
        save_all_issues(current_issues, all_issues_path)

    # 결과 DataFrame 반환
    if changes:
        df = pd.DataFrame(changes)
        df['변경 시간'] = pd.to_datetime(df['변경 시간'], format=time_format)

        def convert_to_datetime(value):
            if isinstance(value, str):
                try:
                    return pd.to_datetime(value, format=time_format)
                except (ValueError, TypeError):
                    return value
            return value

        df['변경 전 내용'] = df['변경 전 내용'].apply(convert_to_datetime)
        df['변경 후 내용'] = df['변경 후 내용'].apply(convert_to_datetime)

        # Committer와 Swarm Link, 담당자가 없는 경우 기본값 설정
        if 'Committer' not in df.columns:
            df['Committer'] = 'Unknown'
        if 'Swarm Link' not in df.columns:
            df['Swarm Link'] = ''
        if '담당자' not in df.columns:
            df['담당자'] = 'Unknown'  # 담당자 기본값 설정

        return df
    else:
        return pd.DataFrame([])

def load_all_issues(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("all_issues.json 파일이 없습니다. 빈 데이터로 초기화합니다.")
        return {}

def save_all_issues(data, file_path):
    temp_filename = os.path.join(os.getcwd(), 'all_issues.json.temp')
    try:
        with open(temp_filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        shutil.move(temp_filename, file_path)
    except Exception as e:
        print(f"all_issues.json 저장 중 오류 발생: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = JiraTrackerApp(root)
    root.mainloop()
