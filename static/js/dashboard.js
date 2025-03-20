document.addEventListener('DOMContentLoaded', function() {
    // Initialize DataTable
    const table = $('#inquiriesTable').DataTable({
        processing: true,
        serverSide: true,
        ajax: {
            url: '/api/inquiries',
            data: function(d) {
                d.status_filter = $('#statusFilter').val();
            }
        },
        columns: [
            { data: 'id' },
            { data: 'date_received' },
            { data: 'client_name' },
            { data: 'email' },
            { data: 'phone' },
            { data: 'travel_dates' },
            { data: 'trip_cost' },
            { 
                data: 'status',
                render: function(data, type, row) {
                    if (type === 'display') {
                        let badgeClass = 'bg-warning';
                        if (data === 'Complete') {
                            badgeClass = 'bg-success';
                        } else if (data === 'Error') {
                            badgeClass = 'bg-danger';
                        }
                        return `<span class="badge ${badgeClass}">${data}</span>`;
                    }
                    return data;
                }
            },
            { 
                data: 'actions',
                orderable: false,
                searchable: false
            }
        ],
        order: [[1, 'desc']],  // Default order by Date Received (newest first)
        pageLength: 10,
        responsive: true,
        language: {
            search: "Quick Search:",
            emptyTable: "No booking inquiries available",
            zeroRecords: "No matching inquiries found",
            info: "Showing _START_ to _END_ of _TOTAL_ inquiries",
            infoEmpty: "Showing 0 to 0 of 0 inquiries",
            infoFiltered: "(filtered from _MAX_ total inquiries)"
        },
        dom: '<"row"<"col-md-6"l><"col-md-6"f>>rtip'
    });

    // Apply custom search when using the search box
    $('#searchBox').on('keyup', function() {
        table.search(this.value).draw();
    });

    // Apply status filter
    $('#statusFilter').on('change', function() {
        table.ajax.reload();
    });

    // When status filter changes, update URL params
    $('#statusFilter').on('change', function() {
        const status = $(this).val();
        if (status !== 'All') {
            updateUrlParameter('status', status);
        } else {
            removeUrlParameter('status');
        }
    });

    // When search box changes, update URL params
    let searchTimeout;
    $('#searchBox').on('keyup', function() {
        clearTimeout(searchTimeout);
        const search = $(this).val();
        
        searchTimeout = setTimeout(function() {
            if (search) {
                updateUrlParameter('search', search);
            } else {
                removeUrlParameter('search');
            }
        }, 500);
    });

    // Helper function to update URL parameters
    function updateUrlParameter(key, value) {
        const url = new URL(window.location.href);
        url.searchParams.set(key, value);
        window.history.replaceState({}, '', url);
    }

    // Helper function to remove URL parameters
    function removeUrlParameter(key) {
        const url = new URL(window.location.href);
        url.searchParams.delete(key);
        window.history.replaceState({}, '', url);
    }
});
